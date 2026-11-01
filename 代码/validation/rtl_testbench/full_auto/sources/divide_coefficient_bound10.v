`timescale 1ns/1ps
// Coefficient-only exact unsigned division. At each accepted request:
// denominator fits56bits, numerator fits63bits and numerator/denominator<1024.
// The known-zero high quotient prefix is skipped, not approximated. Ten
// restoring steps plus the original nearest-even rounding remain. Generic
// divide_round is unchanged. Invalid internal contract returns an error-sized
// quotient, which the coefficient generator rejects; simulation also asserts.
module divide_coefficient_bound10(input clk,input rst_n,input start,input[79:0] numerator,
 input[63:0] denominator,output reg busy,output reg done,output reg[79:0] quotient);
 reg[9:0] work;reg[55:0] remainder,den;reg[3:0] left;
 wire[56:0] trial={remainder,work[9]};
 wire[57:0] difference={1'b0,trial}-{2'b0,den};
 wire take=!difference[57];
 wire[55:0] next_rem=take ? difference[55:0]:trial[55:0];
 wire[9:0] next_work={work[8:0],take};
 wire[56:0] doubled_rem={next_rem,1'b0};
 wire round_up=doubled_rem>{1'b0,den} || (doubled_rem=={1'b0,den} && next_work[0]);
 wire[10:0] rounded={1'b0,next_work}+round_up;
 wire contract_valid=numerator[79:63]==0 && denominator[63:56]==0 && denominator!=0 &&
                      {6'd0,numerator[79:10]} < {12'd0,denominator};
 // synthesis translate_off
 always @(posedge clk)if(rst_n && start && !busy && !contract_valid)
   $fatal(1,"coefficient bounded divider contract num=%h den=%h",numerator,denominator);
 // synthesis translate_on
 always @(posedge clk)begin
   done<=0;
   if(!rst_n)begin busy<=0;done<=0;quotient<=0;work<=0;remainder<=0;den<=1;left<=0;end
   else if(start && !busy)begin
     if(!contract_valid)begin quotient<={80{1'b1}};done<=1;busy<=0;end
     else begin work<=numerator[9:0];remainder<=numerator[65:10];den<=denominator[55:0];left<=10;busy<=1;end
   end else if(busy)begin
     work<=next_work;remainder<=next_rem;left<=left-1'b1;
     if(left==1)begin quotient<={69'd0,rounded};done<=1;busy<=0;end
   end
 end
endmodule
