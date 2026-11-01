`timescale 1ns/1ps
// K=4 diagnostic baseline. Two II=1 MAC lanes share one immutable history.
// Four 1024x12 correction stores each hold old/new slots. Caller owns the
// slots and must not overwrite any slot used by a sample or an ongoing fade.
module dual_lane_correction_mac(
 input clk,rst_n,in_valid,input signed[15:0] in_data,input[31:0] in_seq,
 input fading,new_only,input[5:0] alpha,
 input coef_we,input[2:0] coef_branch,input[8:0] coef_addr,input signed[17:0] coef_data,
 output ready,output reg out_valid,output reg signed[15:0] out_data,
 output reg[31:0] out_seq,dropped,saturated,output reg[15:0] max_cycles,output reg busy);
 reg signed[15:0] hist_a[0:1023] /* synthesis syn_ramstyle="block_ram" */;
 reg signed[15:0] hist_b[0:1023] /* synthesis syn_ramstyle="block_ram" */;
 reg signed[11:0] c0[0:1023] /* synthesis syn_ramstyle="block_ram" */;
 reg signed[11:0] c1[0:1023] /* synthesis syn_ramstyle="block_ram" */;
 reg signed[11:0] c2[0:1023] /* synthesis syn_ramstyle="block_ram" */;
 reg signed[11:0] c3[0:1023] /* synthesis syn_ramstyle="block_ram" */;
 reg[9:0] wr,ptr,filled;reg[8:0] tap;reg[1:0] group_index;
 reg[3:0] state;reg fade,slot;reg[5:0] weight;
 reg signed[15:0] ha,hb,delay_sample;
 reg signed[11:0] q0,q1,q2,q3;
 reg mask_a,mask_b,center,read_valid,read_last,product_valid,product_last;
 reg signed[34:0] product0,product1;
 reg signed[43:0] acc,oldacc;reg signed[49:0] blend,delta,round_input;
 reg[2:0] blend_bit;reg[15:0] cycles;reg[31:0] sequence_hold;
 wire[9:0] ra=ptr-{1'b0,tap},rb=ptr-(10'd640-{1'b0,tap});
 wire[9:0] ca={fade ? group_index[1]:slot,tap};
 wire[9:0] wa={coef_branch[2],coef_addr};
 wire signed[16:0] av=mask_a?{ha[15],ha}:17'sd0;
 wire signed[16:0] bv=mask_b&&!center?{hb[15],hb}:17'sd0;
 wire signed[16:0] pair_value=av+bv;
 wire signed[11:0] selected0=group_index[0]?q2:q0;
 wire signed[11:0] selected1=group_index[0]?q3:q1;
 wire signed[17:0] coeff0=-$signed({{6{selected0[11]}},selected0});
 wire signed[17:0] coeff1=-$signed({{6{selected1[11]}},selected1});
 wire signed[43:0] identity_term=$signed({{28{delay_sample[15]}},delay_sample})<<<16;
 wire signed[43:0] complete_acc=acc+identity_term;
 assign ready=!busy;
 always @(posedge clk)begin
   if(in_valid&&!busy&&rst_n)begin hist_a[wr]<=in_data;hist_b[wr]<=in_data;end
   if(state==1)begin
     ha<=hist_a[ra];hb<=hist_b[rb];
     q0<=c0[ca];q1<=c1[ca];q2<=c2[ca];q3<=c3[ca];
   end
   if(coef_we)case(coef_branch[1:0])
     0:c0[wa]<=coef_data[11:0];1:c1[wa]<=coef_data[11:0];
     2:c2[wa]<=coef_data[11:0];3:c3[wa]<=coef_data[11:0];
   endcase
 end
 reg[49:0] magnitude,rounded;reg[20:0] remainder;
 always @*begin
   magnitude=round_input[49]?-round_input:round_input;
   rounded=magnitude>>21;remainder=magnitude[20:0];
   if(remainder>21'h100000||(remainder==21'h100000&&rounded[0]))rounded=rounded+1'b1;
 end
 always @(posedge clk)begin
   out_valid<=0;read_valid<=0;product_valid<=read_valid;product_last<=read_last;
   if(read_valid)begin
     product0<=pair_value*coeff0;product1<=pair_value*coeff1;
     if(center)delay_sample<=mask_a?ha:16'sd0;
   end
   if(product_valid)acc<=acc+{{9{product0[34]}},product0}+{{9{product1[34]}},product1};
   if(!rst_n)begin
     wr<=0;ptr<=0;filled<=0;tap<=0;group_index<=0;state<=0;fade<=0;slot<=0;weight<=0;
     mask_a<=0;mask_b<=0;center<=0;read_valid<=0;read_last<=0;product_valid<=0;product_last<=0;
     product0<=0;product1<=0;delay_sample<=0;acc<=0;oldacc<=0;blend<=0;delta<=0;
     round_input<=0;blend_bit<=0;cycles<=0;sequence_hold<=0;out_data<=0;out_seq<=0;
     dropped<=0;saturated<=0;max_cycles<=0;busy<=0;out_valid<=0;
   end else begin
     if(in_valid&&busy)dropped<=dropped+1'b1;
     if(busy)cycles<=cycles+1'b1;
     case(state)
       0:if(in_valid)begin
         busy<=1;cycles<=1;ptr<=wr;wr<=wr+1'b1;if(filled<641)filled<=filled+1'b1;
         sequence_hold<=in_seq;fade<=fading;slot<=new_only;weight<=alpha;
         tap<=0;group_index<=0;acc<=0;state<=1;
       end
       1:begin
         mask_a<=({1'b0,tap}<filled);mask_b<=((10'd640-{1'b0,tap})<filled);center<=tap==320;
         read_valid<=1;read_last<=tap==320;
         if(tap==320)state<=2;else tap<=tap+1'b1;
       end
       2:if(product_valid&&product_last)state<=3;
       3:begin
         if(!group_index[0])begin group_index<=group_index+1'b1;tap<=0;state<=1;end
         else if(fade&&group_index==1)begin
           oldacc<=complete_acc;acc<=0;group_index<=2;tap<=0;state<=1;
         end else if(fade)begin
           blend<={{6{oldacc[43]}},oldacc}<<<5;
           delta<={{6{complete_acc[43]}},complete_acc}-{{6{oldacc[43]}},oldacc};blend_bit<=0;state<=5;
         end else begin round_input<={{6{complete_acc[43]}},complete_acc}<<<5;state<=7;end
       end
       5:begin
         if(weight[blend_bit])blend<=blend+(delta<<<blend_bit);
         if(blend_bit==5)state<=6;else blend_bit<=blend_bit+1'b1;
       end
       6:begin round_input<=blend;state<=7;end
       7:begin
         if((!round_input[49]&&rounded>32767)||(round_input[49]&&rounded>32768))begin
           out_data<=round_input[49]?-16'sd32768:16'sd32767;saturated<=saturated+1'b1;
         end else out_data<=round_input[49]?-$signed(rounded[15:0]):$signed(rounded[15:0]);
         out_seq<=sequence_hold;out_valid<=1;busy<=0;state<=0;if(cycles>max_cycles)max_cycles<=cycles;
       end
     endcase
   end
 end
endmodule
