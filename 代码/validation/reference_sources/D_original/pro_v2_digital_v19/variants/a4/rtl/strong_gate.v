`timescale 1ns/1ps
// Frozen v1.4: native raw bin / max(two ten-bin medians) >= 10^(21/10).
// P is unsigned Q58. Two middle values are kept as a sum (twice median),
// including half-LSB medians. Rank counting reads only the frozen raw frame.
module strong_gate(input clk,input rst_n,input start,input numeric_valid,
 input[8:0] peak_bin,output reg[8:0] power_addr,input[63:0] power_data,
 output reg busy,output reg done,output reg strong_pass,output reg result_valid,
 output reg[64:0] background_twice,output reg[63:0] peak_power,
 output reg[15:0] last_cycles);
 localparam[39:0] RATIO_Q32=40'd540704347176;
 localparam[63:0] POWER_FLOOR=64'd29;
 reg[4:0] state;reg[8:0] center;reg side;
 reg[3:0] i,j,rank;reg[63:0] current,median4,median5;
 reg[64:0] left_twice;reg[5:0] bit_index;
 reg[104:0] shifted_background,threshold_product;
 reg[15:0] cycles;
 wire[64:0] median_twice={1'b0,median4}+{1'b0,median5};
 wire[64:0] maximum_median=left_twice>median_twice?left_twice:median_twice;
 wire[63:0] floored_peak=peak_power<POWER_FLOOR?POWER_FLOOR:peak_power;
 wire[104:0] scaled_peak={8'd0,floored_peak,33'd0};
 function[8:0] reference_address;
   input[3:0] index;input right_side;
   begin reference_address=right_side?center+9'd3+{5'd0,index}:center-9'd3-{5'd0,index};end
 endfunction
 always @(posedge clk)begin
   done<=0;
   if(!rst_n)begin
     state<=0;center<=0;side<=0;i<=0;j<=0;rank<=0;current<=0;median4<=0;median5<=0;
     left_twice<=0;bit_index<=0;shifted_background<=0;threshold_product<=0;
     busy<=0;done<=0;strong_pass<=0;result_valid<=0;background_twice<=0;peak_power<=0;
     power_addr<=0;cycles<=0;last_cycles<=0;
   end else begin
     if(busy)cycles<=cycles+1'b1;
     case(state)
       0:if(start)begin
         strong_pass<=0;result_valid<=0;cycles<=1;
         if(!numeric_valid || peak_bin<13 || peak_bin>243)begin done<=1;last_cycles<=1;end
         else begin center<=peak_bin;power_addr<=peak_bin;busy<=1;state<=1;end
       end
       1:state<=2;
       2:begin peak_power<=power_data;side<=0;i<=0;median4<=0;median5<=0;state<=3;end
       3:begin power_addr<=reference_address(i,side);state<=4;end
       4:state<=5;
       5:begin current<=power_data;j<=0;rank<=0;state<=6;end
       6:begin power_addr<=reference_address(j,side);state<=7;end
       7:state<=8;
       8:begin
         if(power_data<current || (power_data==current && j<i))rank<=rank+1'b1;
         if(j==9)state<=9;else begin j<=j+1'b1;state<=6;end
       end
       9:begin
         if(rank==4)median4<=current;if(rank==5)median5<=current;
         if(i==9)state<=10;else begin i<=i+1'b1;state<=3;end
       end
       10:begin
         if(!side)begin left_twice<=median_twice;side<=1;i<=0;median4<=0;median5<=0;state<=3;end
         else begin background_twice<=maximum_median<65'd58 ? 65'd58 : maximum_median;state<=11;end
       end
       11:begin shifted_background<={40'd0,background_twice};threshold_product<=0;bit_index<=0;state<=12;end
       12:begin
         if(RATIO_Q32[bit_index])threshold_product<=threshold_product+shifted_background;
         shifted_background<=shifted_background<<1;
         if(bit_index==39)state<=13;else bit_index<=bit_index+1'b1;
       end
       13:begin
         strong_pass<=scaled_peak>=threshold_product;result_valid<=1;
         busy<=0;done<=1;last_cycles<=cycles;state<=0;
       end
       default:state<=0;
     endcase
   end
 end
endmodule
