`timescale 1ns/1ps
// Same basis/RNE as basis_generator.v. Cosine and prototype share one ROM.
module coefficient_generator #(parameter GRID_ALIGNED=1)(input clk,input rst_n,input start,input[31:0] frequency_word,
 output reg busy,output reg done,output reg error,
 output reg coef_valid,output reg[8:0] coef_address,output reg signed[17:0] coef_value,
 output reg[31:0] last_cycles,output[63:0] normalization,input service_start,input[12:0] service_phase,output service_done,output signed[17:0] service_value);
 reg[3:0] state;reg[8:0] tap;reg[12:0] phase,word;
 reg[12:0] cycles;reg cos_start,proto_start;
 wire cos_busy,cos_done,proto_valid;wire signed[17:0] cosine;wire[7:0] proto_q;
 reg[7:0] p;
 wire lookup_start=busy ? cos_start:service_start;
 wire[12:0] lookup_phase=busy ? phase:service_phase;
 assign service_done=cos_done && !busy;assign service_value=cosine;
 cosine_basis_lossless lookup(clk,rst_n,lookup_start,lookup_phase,cos_busy,cos_done,cosine,proto_start,tap,proto_valid,proto_q);
 // synthesis translate_off
 always @(posedge clk)if(rst_n && start && (cos_busy || service_start))$fatal(1,"cosine service ownership violation");
 // synthesis translate_on
 wire signed[25:0] product=$signed({1'b0,p})*cosine;
 wire[25:0] magnitude=product[25] ? -product:product;
 wire[10:0] rounded=magnitude[25:15]+((magnitude[14:0]>15'h4000) ||
                             (magnitude[14:0]==15'h4000 && magnitude[15]));
 assign normalization=64'd0;
 always @(posedge clk)begin
   cos_start<=0;proto_start<=0;done<=0;coef_valid<=0;
   if(!rst_n)begin
     busy<=0;done<=0;error<=0;coef_valid<=0;coef_address<=0;coef_value<=0;
     last_cycles<=0;cycles<=0;tap<=0;phase<=0;word<=0;state<=0;cos_start<=0;proto_start<=0;p<=0;
   end else begin
     if(busy)cycles<=cycles+1'b1;
     // synthesis translate_off
     if(busy && cycles>=13'd4600)$fatal(1,"packed generator cycle bound violated");
     // synthesis translate_on
     case(state)
       0:if(start)begin
         error<=0;cycles<=1;
         if(frequency_word==0 || frequency_word>=32'h80000000 || frequency_word[18:0]!=0)begin error<=1;done<=1;end
         else begin word<=frequency_word[31:19];phase<=(frequency_word[31:19]<<8)+(frequency_word[31:19]<<6);tap<=0;busy<=1;state<=1;end
       end
       1:begin proto_start<=1;state<=4;end
       4:if(proto_valid)begin p<=proto_q;state<=5;end
       5:begin cos_start<=1;state<=2;end
       2:if(cos_done)state<=3;
       3:begin
         coef_valid<=1;coef_address<=tap;
         coef_value<=product[25] ? -$signed({7'd0,rounded}):$signed({7'd0,rounded});
         if(tap==320)begin busy<=0;done<=1;last_cycles<=cycles;state<=0;end
         else begin tap<=tap+1'b1;phase<=phase-word;state<=1;end
       end
       default:state<=0;
     endcase
   end
 end
endmodule

module cosine_basis_lossless(input clk,input rst_n,input start,input[12:0] phase,
 output reg busy,output reg done,output reg signed[17:0] value,
 input proto_start,input[8:0] proto_addr,output reg proto_valid,output reg[7:0] proto_q);
 reg[15:0] rom[0:1023] /* synthesis syn_romstyle="block_rom" */;
 initial $readmemb("rom/cosine_prototype_packed.mem",rom);
 reg[15:0] q;reg[9:0] address;reg[3:0] state;
 reg[16:0] base;reg[31:0] params;reg[2:0] offset;
 reg endpoint,negative,zero_base,proto_odd;
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
   q<=rom[address];done<=0;proto_valid<=0;
   if(!rst_n)begin state<=0;busy<=0;value<=0;address<=0;base<=0;params<=0;offset<=0;endpoint<=0;negative<=0;zero_base<=0;proto_q<=0;proto_odd<=0;end
   else case(state)
     0:if(proto_start)begin busy<=1;address<={proto_addr[8:1],2'd3};proto_odd<=proto_addr[0];state<=6;end
     else if(start)begin
       busy<=1;endpoint<=angle[11];negative<=phase[12]^phase[11];
       zero_base<=angle[10:3]==0;offset<=angle[2:0];address<={angle[10:3],2'd0};state<=1;
     end
     1:begin address<=address+1'b1;state<=2;end
     2:begin base<=zero_base ? 17'd65536:{1'b0,q};address<=address+1'b1;state<=3;end
     3:begin params[15:0]<=q;state<=4;end
     4:begin params[31:16]<=q;state<=5;end
     5:begin value<=negative ? -$signed(magnitude):$signed(magnitude);busy<=0;done<=1;state<=0;end
     6:state<=7;
     7:begin proto_q<=(proto_odd ? q[13:7]:q[6:0])+8'd53;proto_valid<=1;busy<=0;state<=0;end
   endcase
 end
endmodule
