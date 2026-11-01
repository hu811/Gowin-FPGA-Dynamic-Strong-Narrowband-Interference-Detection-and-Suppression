`timescale 1ns/1ps
// Diagnostic baseline: ONE multiplier, shared continuous history, symmetric
// preadd, II=2. All branch sums retain Q16/44-bit precision; only final output
// is rounded/saturated. This is not K parallel full FIRs. Whole-bank fades use
// exactly the same old/new alpha as the production merged FIR.
module shared_correction_mac #(parameter K=4)(
 input clk,rst_n,in_valid,input signed[15:0] in_data,input[31:0] in_seq,
 input fading,input new_only,input[5:0] alpha,input coef_we,input[2:0] coef_branch,
 input[8:0] coef_addr,input signed[17:0] coef_data,
 output ready,output reg out_valid,output reg signed[15:0] out_data,
 output reg[31:0] out_seq,output reg[15:0] max_cycles,output reg busy);
 reg signed[15:0] ha_mem[0:1023],hb_mem[0:1023];
 reg signed[17:0] coeff[0:4095];
 reg[9:0] wr,ptr,filled;reg[8:0] tap;reg[2:0] branch;
 reg[3:0] state;reg pass,fade,pvalid;reg[5:0] weight;
 reg signed[15:0] ha,hb;reg signed[17:0] cq;
 reg ma,mb,center;reg signed[34:0] product;
 reg signed[43:0] acc,oldacc;reg signed[49:0] blend,delta,round_input;
 reg[2:0] blend_bit;reg[15:0] cycles;reg[31:0] seq;
 wire[9:0] ra=ptr-{1'b0,tap},rb=ptr-(10'd640-{1'b0,tap});
 wire signed[16:0] av=ma?{ha[15],ha}:17'sd0;
 wire signed[16:0] bv=mb&&!center?{hb[15],hb}:17'sd0;
 wire signed[16:0] pair_value=av+bv;
 wire[2:0] cb=branch+((pass||new_only)?K:0);
 assign ready=!busy;
 always @(posedge clk)begin
   if(in_valid&&!busy&&rst_n)begin ha_mem[wr]<=in_data;hb_mem[wr]<=in_data;end
   if(state==1)begin ha<=ha_mem[ra];hb<=hb_mem[rb];cq<=-coeff[{cb,tap}];end
   if(coef_we)coeff[{coef_branch,coef_addr}]<=coef_data;
 end
 reg[49:0] magnitude,rounded;reg[20:0] remainder;
 always @*begin
   magnitude=round_input[49]?-round_input:round_input;
   rounded=magnitude>>21;remainder=magnitude[20:0];
   if(remainder>21'h100000||(remainder==21'h100000&&rounded[0]))rounded=rounded+1'b1;
 end
 always @(posedge clk)begin
  out_valid<=0;
  if(!rst_n)begin
   state<=0;wr<=0;ptr<=0;filled<=0;tap<=0;branch<=0;pass<=0;fade<=0;
   pvalid<=0;ma<=0;mb<=0;center<=0;acc<=0;oldacc<=0;blend<=0;delta<=0;
   round_input<=0;blend_bit<=0;busy<=0;out_data<=0;out_seq<=0;max_cycles<=0;cycles<=0;seq<=0;weight<=0;
  end else begin
   if(busy)cycles<=cycles+1'b1;
   case(state)
    0:if(in_valid)begin
      busy<=1;cycles<=1;ptr<=wr;wr<=wr+1'b1;if(filled<641)filled<=filled+1'b1;
      seq<=in_seq;fade<=fading;weight<=alpha;tap<=0;branch<=0;pass<=0;acc<=0;pvalid<=0;state<=1;
    end
    1:begin
      ma<=({1'b0,tap}<filled);mb<=((10'd640-{1'b0,tap})<filled);center<=tap==320;
      if(pvalid)acc<=acc+{{9{product[34]}},product};state<=2;
    end
    2:begin
      product<=pair_value*cq;pvalid<=1;
      if(tap==320)state<=3;else begin tap<=tap+1'b1;state<=1;end
    end
    3:begin
      // Delay(input) is added once, rather than using a full identity FIR.
      acc<=acc+{{9{product[34]}},product}+
        ((branch==K-1&&ma)?($signed({{28{ha[15]}},ha})<<<16):44'sd0);
      pvalid<=0;state<=4;
    end
    4:if(branch!=K-1)begin branch<=branch+1'b1;tap<=0;state<=1;end
      else if(fade&&!pass)begin oldacc<=acc;acc<=0;pass<=1;branch<=0;tap<=0;state<=1;end
      else if(fade)begin blend<={{6{oldacc[43]}},oldacc}<<<5;
        delta<={{6{acc[43]}},acc}-{{6{oldacc[43]}},oldacc};blend_bit<=0;state<=5;end
      else begin round_input<={{6{acc[43]}},acc}<<<5;state<=7;end
    5:begin
      if(weight[blend_bit])blend<=blend+(delta<<<blend_bit);
      if(blend_bit==5)state<=6;else blend_bit<=blend_bit+1'b1;
    end
    6:begin round_input<=blend;state<=7;end
    7:begin
      if((!round_input[49]&&rounded>32767)||(round_input[49]&&rounded>32768))
        out_data<=round_input[49]?-16'sd32768:16'sd32767;
      else out_data<=round_input[49]?-$signed(rounded[15:0]):$signed(rounded[15:0]);
      out_seq<=seq;out_valid<=1;busy<=0;state<=0;if(cycles>max_cycles)max_cycles<=cycles;
    end
   endcase
  end
 end
endmodule
