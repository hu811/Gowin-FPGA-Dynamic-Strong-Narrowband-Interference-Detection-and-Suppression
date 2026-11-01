`timescale 1ns/1ps
// Unsigned 80/64 restoring division; nearest-even quotient, fixed 81 cycles.
module divide_round(input clk,input rst_n,input start,input[79:0] numerator,
 input[63:0] denominator,output reg busy,output reg done,output reg[79:0] quotient);
 reg[79:0] work;reg[64:0] remainder;reg[63:0] den;reg[6:0] left;
 wire[64:0] trial={remainder[63:0],work[79]};
 wire[65:0] difference={1'b0,trial}-{2'b0,den};
 wire ge=!difference[65];
 wire[64:0] next_rem=ge ? difference[64:0]:trial;
 wire[79:0] next_work={work[78:0],ge};
 wire round_up=({next_rem,1'b0}>{2'b0,den}) ||
               (({next_rem,1'b0}=={2'b0,den}) && next_work[0]);
 always @(posedge clk)begin
   done<=0;
   if(!rst_n)begin busy<=0;done<=0;quotient<=0;work<=0;remainder<=0;den<=1;left<=0;end
   else if(start && !busy)begin work<=numerator;den<=denominator;remainder<=0;left<=80;busy<=1;end
   else if(busy)begin
     work<=next_work;remainder<=next_rem;left<=left-1'b1;
     if(left==1)begin quotient<=next_work+{{79{1'b0}},round_up};done<=1;busy<=0;end
   end
 end
endmodule
