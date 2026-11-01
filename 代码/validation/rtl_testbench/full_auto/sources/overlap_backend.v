`timescale 1ns/1ps
// Experimental standalone backend. Cache is the original signed Q16 half FIR.
// The caller must bind cache ownership and snapshot_tag to production versions.
// Failure means NO bank is authorized; it must never be treated as bypass.
module overlap_backend #(parameter MAX_CENTERS=4, ITERATIONS=12)(
 input clk,input rst_n,input start,input[MAX_CENTERS-1:0] mask,
 input[13*MAX_CENTERS-1:0] centers,input[31:0] snapshot_tag,
 output reg busy,output reg done,output reg error,output reg[31:0] result_tag,
 output[2:0] cache_track,output[8:0] cache_addr,input signed[15:0] cache_q,
 output reg coef_valid,output reg[8:0] coef_addr,output reg signed[17:0] coef_q,
 output[24*MAX_CENTERS-1:0] weights_q20,output reg[31:0] cycles);
 localparam signed[23:0] ONE=24'sd1048576;
 reg[MAX_CENTERS-1:0] live;
 reg[13*MAX_CENTERS-1:0] freq;
 reg[5:0] state;
 reg[2:0] row,col;
 reg[8:0] tap;
 reg[4:0] iteration;
 reg signed[23:0] matrix[0:MAX_CENTERS*MAX_CENTERS-1];
 reg signed[23:0] weight[0:MAX_CENTERS-1],next_weight[0:MAX_CENTERS-1];
 reg signed[23:0] ma,mb;
 wire signed[47:0] product=ma*mb;
 reg signed[55:0] sum;
 reg signed[23:0] residual;
 reg[31:0] row_norm;
 reg cos_start;wire cos_busy,cos_done;wire signed[17:0] cosine;
 reg[12:0] phase;
 wire[12:0] center=freq[row*13+:13];
 cosine_lossless lookup(clk,rst_n,cos_start,phase,cos_busy,cos_done,cosine);
 assign cache_track=col;assign cache_addr=tap;
 genvar g;generate for(g=0;g<MAX_CENTERS;g=g+1)begin:out_weight
   assign weights_q20[g*24+:24]=weight[g];
 end endgenerate
 function signed[55:0] rne;
   input signed[55:0] value;input[5:0] shift;
   reg[55:0] magnitude,q,rem,half;
   begin
     magnitude=value[55] ? -value:value;q=magnitude>>shift;
     rem=magnitude-(q<<shift);half=56'd1<<(shift-1);
     if(rem>half || (rem==half && q[0]))q=q+1'b1;
     rne=value[55] ? -$signed(q):$signed(q);
   end
 endfunction
 wire signed[55:0] response=rne(sum,6'd12);
 wire signed[55:0] dot=rne(sum,6'd20);
 wire signed[55:0] weighted_tap=sum;
 wire signed[55:0] rounded_product=rne({{8{product[47]}},product},6'd20);
 wire signed[23:0] cell_error=(row==col ? ONE:24'sd0)-matrix[row*MAX_CENTERS+col];
 wire[23:0] absolute_cell=cell_error[23] ? -cell_error:cell_error;
 integer k;
 always @(posedge clk)begin
   cos_start<=0;done<=0;coef_valid<=0;
   if(!rst_n)begin
     busy<=0;error<=0;state<=0;live<=0;freq<=0;result_tag<=0;cycles<=0;
     row<=0;col<=0;tap<=0;iteration<=0;ma<=0;mb<=0;sum<=0;
     residual<=0;row_norm<=0;phase<=0;coef_addr<=0;coef_q<=0;cos_start<=0;
     for(k=0;k<MAX_CENTERS;k=k+1)begin weight[k]<=0;next_weight[k]<=0;end
   end else begin
     if(busy)cycles<=cycles+1'b1;
     case(state)
       0:if(start)begin
         live<=mask;freq<=centers;result_tag<=snapshot_tag;error<=0;busy<=1;
         cycles<=0;row<=0;col<=0;tap<=0;sum<=0;iteration<=0;state<=1;
         for(k=0;k<MAX_CENTERS;k=k+1)weight[k]<=mask[k] ? ONE:24'sd0;
       end
       1:begin
         if(!live[row] || !live[col])begin matrix[row*MAX_CENTERS+col]<=0;state<=5;end
         else begin
           // tap 0 represents n=-320; even cosine makes the sign immaterial.
           phase<=(center<<8)+(center<<6);tap<=0;sum<=0;state<=2;
         end
       end
       2:begin cos_start<=1;state<=3;end
       3:if(cos_done)begin ma<={{8{cache_q[15]}},cache_q};mb<={{6{cosine[17]}},cosine};state<=4;end
       4:begin
         sum<=sum+($signed(product)<<<(tap==320 ? 0:1));
         if(tap==320)state<=6;
         else begin tap<=tap+1'b1;phase<=phase-center;state<=2;end
       end
       6:begin
         if(response>56'sd8388607 || response< -56'sd8388608)begin error<=1;state<=31;end
         else begin matrix[row*MAX_CENTERS+col]<=response[23:0];state<=5;end
       end
       5:begin
         if(col==MAX_CENTERS-1)begin
           col<=0;
           if(row==MAX_CENTERS-1)begin row<=0;row_norm<=0;state<=7;end
           else begin row<=row+1'b1;state<=1;end
         end else begin col<=col+1'b1;state<=1;end
       end
       7:begin
         // Contractivity is checked, not presumed for unresolved close peaks.
         if(live[row])row_norm<=row_norm+absolute_cell;
         if(col==MAX_CENTERS-1)state<=8;else col<=col+1'b1;
       end
       8:begin
         if(row_norm>=32'd524288)begin error<=1;state<=31;end
         else if(row==MAX_CENTERS-1)begin row<=0;col<=0;sum<=0;state<=9;end
         else begin row<=row+1'b1;col<=0;row_norm<=0;state<=7;end
       end
       9:begin ma<=matrix[row*MAX_CENTERS+col];mb<=weight[col];state<=10;end
       10:begin sum<=sum+$signed(product);if(col==MAX_CENTERS-1)state<=11;else begin col<=col+1'b1;state<=9;end end
       11:begin
         residual<=(live[row] ? ONE:24'sd0)-dot[23:0];state<=12;
       end
       12:begin
         next_weight[row]<=weight[row]+residual;
         if(weight[row]+residual>24'sd2097152 || weight[row]+residual< -24'sd2097152)begin error<=1;state<=31;end
         else if(iteration==ITERATIONS)begin
           if(residual>24'sd64 || residual< -24'sd64)begin error<=1;state<=31;end
           else if(row==MAX_CENTERS-1)begin col<=0;tap<=0;sum<=0;state<=15;end
           else begin row<=row+1'b1;col<=0;sum<=0;state<=9;end
         end else if(row==MAX_CENTERS-1)state<=13;
         else begin row<=row+1'b1;col<=0;sum<=0;state<=9;end
       end
       13:begin
         for(k=0;k<MAX_CENTERS;k=k+1)weight[k]<=next_weight[k];
         iteration<=iteration+1'b1;row<=0;col<=0;sum<=0;state<=9;
       end
       15:state<=16; // original cache synchronous-read latency
       16:begin ma<={{8{cache_q[15]}},cache_q};mb<=weight[col];state<=17;end
       17:begin
         if(live[col])sum<=sum+rounded_product;
         if(col==MAX_CENTERS-1)state<=18;else begin col<=col+1'b1;state<=15;end
       end
       18:begin
         if((tap==320 ? 56'sd65536:56'sd0)-weighted_tap>56'sd131071 ||
            (tap==320 ? 56'sd65536:56'sd0)-weighted_tap< -56'sd131072)begin error<=1;state<=31;end
         else begin
           coef_valid<=1;coef_addr<=tap;coef_q<=(tap==320 ? 18'sd65536:18'sd0)-weighted_tap[17:0];
           if(tap==320)state<=31;else begin tap<=tap+1'b1;col<=0;sum<=0;state<=15;end
         end
       end
       31:begin busy<=0;done<=1;state<=0;end
       default:state<=0;
     endcase
   end
 end
endmodule

// Exact lossless production grid cosine representation; one 1Kx16 block ROM.
module cosine_lossless(input clk,input rst_n,input start,input[12:0] phase,
 output reg busy,output reg done,output reg signed[17:0] value);
 reg[15:0] rom[0:1023] /* synthesis syn_romstyle="block_rom" */;
 initial $readmemb("rom/cos_grid_lossless.mem",rom);
 reg[15:0] q;reg[9:0] address;reg[3:0] state;
 reg[16:0] base;reg[31:0] params;reg[2:0] offset;
 reg endpoint,negative,zero_base;
 reg signed[2:0] residual;
 wire[12:0] angle=phase[11] ? 13'd2048-{2'b0,phase[10:0]}:{2'b0,phase[10:0]};
 wire[8:0] slope_offset=params[5:0]*offset;
 wire signed[10:0] correction=$signed({2'b0,slope_offset})+{{8{residual[2]}},residual};
 wire[17:0] magnitude=endpoint ? 18'd0:base-{{7{correction[10]}},correction};
 always @* case(offset)
   0:residual=params[6+:3];1:residual=params[9+:3];2:residual=params[12+:3];3:residual=params[15+:3];
   4:residual=params[18+:3];5:residual=params[21+:3];6:residual=params[24+:3];7:residual=params[27+:3];
 endcase
 always @(posedge clk)begin
   q<=rom[address];done<=0;
   if(!rst_n)begin state<=0;busy<=0;value<=0;address<=0;base<=0;params<=0;offset<=0;endpoint<=0;negative<=0;zero_base<=0;end
   else case(state)
     0:if(start)begin
       busy<=1;endpoint<=angle[11];negative<=phase[12]^phase[11];
       zero_base<=angle[10:3]==0;offset<=angle[2:0];address<={angle[10:3],2'd0};state<=1;
     end
     1:begin address<=address+1'b1;state<=2;end
     2:begin base<=zero_base ? 17'd65536:{1'b0,q};address<=address+1'b1;state<=3;end
     3:begin params[15:0]<=q;state<=4;end
     4:begin params[31:16]<=q;state<=5;end
     5:begin value<=negative ? -$signed(magnitude):$signed(magnitude);busy<=0;done<=1;state<=0;end
   endcase
 end
endmodule
