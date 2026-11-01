`timescale 1ns/1ps
// Q15/12-round Jacobi math, detector precision unchanged as overlap_backend.v, serialized microstore.
// Matrix 0..15, current weights32..35, next weights40..43 share one RAM.
module overlap_backend_q15_mac #(parameter MAX_CENTERS=4, ITERATIONS=12)(
 input clk,input rst_n,input start,input[MAX_CENTERS-1:0] mask,
 input[13*MAX_CENTERS-1:0] centers,input[31:0] snapshot_tag,
 output reg busy,output reg done,output reg error,output reg[31:0] result_tag,
 output[2:0] cache_track,output[8:0] cache_addr,input signed[15:0] cache_q,
 output reg coef_valid,output reg[8:0] coef_addr,output reg signed[17:0] coef_q,
 output reg[24*MAX_CENTERS-1:0] weights_q20,output[31:0] cycles,output service_start,output[12:0] service_phase,input service_done,input signed[17:0] service_value);
 reg[15:0] cycle_counter;assign cycles={16'd0,cycle_counter};
 localparam signed[23:0] ONE=24'sd32768;
 reg[MAX_CENTERS-1:0] live;
 reg[13*MAX_CENTERS-1:0] freq;
 reg[5:0] state;reg[1:0] row,col;reg[8:0] tap;reg[3:0] iteration;
 reg signed[17:0] ma,mb,residual;

 reg signed[35:0] sum;
 reg[19:0] row_norm;
 reg cos_start;wire cos_busy,cos_done;wire signed[17:0] cosine;
 reg[12:0] phase;wire[12:0] center=freq[row*13+:13];
 assign service_start=cos_start;assign service_phase=phase;
 assign cos_done=service_done;assign cosine=service_value;
 assign cache_track=col;assign cache_addr=tap;
 function signed[35:0] rne;
   input signed[35:0] value;input[5:0] shift;
   reg signed[35:0] rounded;reg[35:0] rem,half;
   begin
     // Arithmetic shift gives floor(value/2^shift), including negative values.
     // Its unsigned low bits are the nonnegative remainder, so ties-to-even
     // needs one increment and never needs wide absolute/sign restoration.
     rounded=value>>>shift;rem=value&((36'd1<<shift)-1'b1);half=36'd1<<(shift-1);
     if(rem>half || (rem==half && rounded[0]))rounded=rounded+1'b1;
     rne=rounded;
   end
 endfunction
 wire signed[35:0] response=rne(sum,6'd17);
 wire signed[35:0] dot;
 v27_q15_dot_rne round_dot(sum,dot);


 wire signed[17:0] q;reg[8:0] ra,wa;reg we;reg signed[17:0] wd;
 wire signed[18:0] cell_error;wire[18:0] absolute_cell;wire[17:0] residual_next;
 generate if(MAX_CENTERS==3)begin:spare_dsp
   wire[18:0] neg_cell;
   v27_dsp_sub_mod #(.WIDTH(19)) err_sub(row==col ? ONE[18:0]:19'd0,{q[17],q},cell_error);
   v27_dsp_sub_mod #(.WIDTH(19)) abs_sub(19'd0,cell_error,neg_cell);
   assign absolute_cell=cell_error[18] ? neg_cell:cell_error;
   v27_dsp_sub_mod #(.WIDTH(18)) residual_sub(live[row] ? ONE[17:0]:18'd0,dot[17:0],residual_next);
 end else begin:fabric
   assign cell_error=(row==col ? ONE:24'sd0)-q;
   assign absolute_cell=cell_error[18] ? -cell_error:cell_error;
   assign residual_next=(live[row] ? ONE:24'sd0)-dot[17:0];
 end endgenerate
 wire signed[35:0] sum_update;
 v27_q15_mac mac(state,tap,ma,mb,sum,sum_update);
 wire[19:0] row_norm_next;
 v27_dsp_add_mod #(.WIDTH(20)) norm_adder(row_norm,{1'b0,absolute_cell},row_norm_next);
 // synthesis translate_off
 initial if(MAX_CENTERS<1 || MAX_CENTERS>4 || ITERATIONS!=12)$fatal(1,"unsupported backend capacity/iterations");
 always @(posedge clk)if(rst_n && busy)begin
   if((state==4 || state==23) && (cache_q>264 || cache_q< -264))$fatal(1,"basis-cache bound violated");
   if(state==23 && (sum>36'sd2112 || sum< -36'sd2112))$fatal(1,"merge accumulator bound violated");
   if(state==13 && (sum>36'sd3221225472 || sum< -36'sd3221225472))$fatal(1,"dot signed18 output bound violated");
   if(cycle_counter>=16'd60000)$fatal(1,"backend cycle bound violated");
   // |basis|<=264,641 taps,|cos|<=65536 gives response sum<2^34;
   // contractive G and |w|<=2*ONE give dot sum<3*2^30. signed36 is safe.
   if(state==4 && (sum>36'sd11090264064 || sum< -36'sd11090264064))$fatal(1,"response accumulator bound violated");
 end
 // synthesis translate_on
 wire signed[18:0] new_weight;
 v27_dsp_add_mod #(.WIDTH(19)) weight_adder({ma[17],ma},{residual[17],residual},new_weight);
 always @*begin
   ra=0;wa=0;we=0;wd=0;
   case(state)
     30:begin we=1;wa=9'd32+row;wd=live[row] ? ONE:24'd0;end
     1:if(!live[row] || !live[col])begin we=1;wa=row*MAX_CENTERS+col;wd=0;end
     6:begin we=1;wa=row*MAX_CENTERS+col;wd=response[17:0];end
     7:ra=row*MAX_CENTERS+col;
     9:ra=row*MAX_CENTERS+col;
     10:ra=9'd32+col;
     13:ra=9'd32+row;
     15:if(iteration!=ITERATIONS)begin we=1;wa=9'd40+row;wd=new_weight[17:0];end
     16:ra=9'd40+row;
     17:begin we=1;wa=9'd32+row;wd=q;end
     21:ra=9'd32+col;
   endcase
 end
 backend_work_sp #(.WIDTH(18)) work_store(clk,we,wa,ra,wd,q);
 always @(posedge clk)begin
   cos_start<=0;done<=0;coef_valid<=0;
   if(!rst_n)begin
     busy<=0;error<=0;state<=0;live<=0;freq<=0;result_tag<=0;cycle_counter<=0;
     row<=0;col<=0;tap<=0;iteration<=0;ma<=0;mb<=0;sum<=0;
     residual<=0;row_norm<=0;phase<=0;coef_addr<=0;coef_q<=0;cos_start<=0;weights_q20<=0;
   end else begin
     if(busy)cycle_counter<=cycle_counter+1'b1;
     case(state)
       0:if(start)begin
         live<=mask;freq<=centers;result_tag<=snapshot_tag;error<=0;busy<=1;
         cycle_counter<=0;row<=0;col<=0;tap<=0;sum<=0;iteration<=0;state<=30;
       end
       30:if(row==MAX_CENTERS-1)begin row<=0;state<=1;end else row<=row+1'b1;
       1:begin
         if(!live[row] || !live[col])state<=5;
         else begin phase<=(center<<8)+(center<<6);tap<=0;sum<=0;state<=2;end
       end
       2:begin cos_start<=1;state<=3;end
       3:if(cos_done)begin ma<={{8{cache_q[15]}},cache_q};mb<={{6{cosine[17]}},cosine};state<=4;end
       4:begin
         sum<=sum_update;
         if(tap==320)state<=6;
         else begin tap<=tap+1'b1;phase<=phase-center;state<=2;end
       end
       6:begin
         if(response>36'sd131071 || response< -36'sd131072)begin error<=1;state<=31;end
         else state<=5;
       end
       5:begin
         if(col==MAX_CENTERS-1)begin
           col<=0;
           if(row==MAX_CENTERS-1)begin row<=0;row_norm<=0;state<=7;end
           else begin row<=row+1'b1;state<=1;end
         end else begin col<=col+1'b1;state<=1;end
       end
       7:state<=8;
       8:begin
         if(live[row])row_norm<=row_norm_next;
         if(col==MAX_CENTERS-1)state<=18;else begin col<=col+1'b1;state<=7;end
       end
       18:begin
         if(row_norm>=20'd16384)begin error<=1;state<=31;end
         else if(row==MAX_CENTERS-1)begin row<=0;col<=0;sum<=0;state<=9;end
         else begin row<=row+1'b1;col<=0;row_norm<=0;state<=7;end
       end
       9:state<=10;
       10:begin ma<=q;state<=11;end
       11:begin mb<=q;state<=12;end
       12:begin sum<=sum_update;if(col==MAX_CENTERS-1)state<=13;else begin col<=col+1'b1;state<=9;end end
       13:begin residual<=residual_next;state<=14;end
       14:begin ma<=q;state<=15;end
       15:begin
         if(new_weight>19'sd65536 || new_weight< -19'sd65536)begin error<=1;state<=31;end
         else if(iteration==ITERATIONS)begin
           if(residual>18'sd2 || residual< -18'sd2)begin error<=1;state<=31;end
           else if(row==MAX_CENTERS-1)begin col<=0;tap<=0;sum<=0;state<=20;end
           else begin row<=row+1'b1;col<=0;sum<=0;state<=9;end
         end else if(row==MAX_CENTERS-1)begin row<=0;state<=16;end
         else begin row<=row+1'b1;col<=0;sum<=0;state<=9;end
       end
       16:state<=17;
       17:begin
         weights_q20[row*24+:24]<=q;
         if(row==MAX_CENTERS-1)begin iteration<=iteration+1'b1;row<=0;col<=0;sum<=0;state<=9;end
         else begin row<=row+1'b1;state<=16;end
       end
       20:state<=21;
       21:begin ma<={{8{cache_q[15]}},cache_q};state<=22;end
       22:begin mb<=q;state<=23;end
       23:begin
         if(live[col])sum<=sum_update;
         if(col==MAX_CENTERS-1)state<=24;else begin col<=col+1'b1;state<=20;end
       end
       24:begin
         if((tap==320 ? 48'sd65536:48'sd0)-sum>48'sd131071 ||
            (tap==320 ? 48'sd65536:48'sd0)-sum< -48'sd131072)begin error<=1;state<=31;end
         else begin
           coef_valid<=1;coef_addr<=tap;coef_q<=(tap==320 ? 18'sd65536:18'sd0)-sum[17:0];
           if(tap==320)state<=31;else begin tap<=tap+1'b1;col<=0;sum<=0;state<=20;end
         end
       end
       31:begin busy<=0;done<=1;state<=0;end
       default:state<=0;
     endcase
   end
 end
endmodule

