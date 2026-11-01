`timescale 1ns/1ps
// Single folded multiplier, II=2 clocks/term. Fade evaluates two passes against
// the same immutable 641-sample window. Mirrored 1024x16 simple-dual-port RAMs.
module fir_mac_core #(parameter INIT0="rom/bypass.mem")(
 input clk,input rst_n,input in_valid,input signed [15:0] in_data,
 input [31:0] in_seq,output ready,
 input [1:0] old_bank,input old_enable,input [1:0] new_bank,input new_enable,
 input [5:0] alpha,input fading,
 input coef_we,input [1:0] coef_bank,input [8:0] coef_addr,input signed [17:0] coef_data,
 output reg out_valid,output reg signed [15:0] out_data,output reg [31:0] out_seq,
 output reg [31:0] dropped,output reg [31:0] saturated,output reg [15:0] max_cycles,
 output reg busy);
 reg signed [15:0] hist_a[0:1023] /* synthesis syn_ramstyle="block_ram" */;
 reg signed [15:0] hist_b[0:1023] /* synthesis syn_ramstyle="block_ram" */;
 // Three logical 321-tap banks share one 1024x18 1W/1R memory.
 // Old/new passes are sequential; only the current pass reads a coefficient.
 // The manager writes an unoccupied logical bank, never an in-flight bank.
 reg signed [17:0] coefficient_store[0:1023] /* synthesis syn_ramstyle="block_ram" */;
 // Initialization-only arrays retain INIT0 compatibility with fixed_top.
 reg signed [17:0] initial_bank0[0:511],initial_bypass[0:511];
 integer init_i;
 initial begin
   $readmemb(INIT0,initial_bank0);
   $readmemb("rom/bypass.mem",initial_bypass);
   for(init_i=0;init_i<1024;init_i=init_i+1)coefficient_store[init_i]=0;
   for(init_i=0;init_i<321;init_i=init_i+1)begin
     coefficient_store[init_i]=initial_bank0[init_i];
     coefficient_store[321+init_i]=initial_bypass[init_i];
     coefficient_store[642+init_i]=initial_bypass[init_i];
   end
 end
 reg [9:0] write_ptr,current_ptr,filled;
 reg [8:0] tap;reg [3:0] state;reg pass,product_valid;
 reg [1:0] b_old,b_new;reg en_old,en_new,do_fade;reg [5:0] a_weight;
 reg [15:0] cycles;reg [31:0] seq_hold;
 reg signed [15:0] ha,hb;
 reg signed [17:0] coefficient_q;
 reg mask_a,mask_b,center;
 reg signed [34:0] product;
 reg signed [43:0] acc,acc_old;
 reg signed [49:0] blend,delta,round_input;
 reg [2:0] blend_bit;
 wire accept=in_valid && !busy;
 wire [9:0] ra=current_ptr-{1'b0,tap};
 wire [9:0] rb=current_ptr-(10'd640-{1'b0,tap});
 wire signed [16:0] av=mask_a ? {ha[15],ha}:17'sd0;
 wire signed [16:0] bv=mask_b && !center ? {hb[15],hb}:17'sd0;
 wire signed [16:0] pair_value=av+bv;
 wire [1:0] selected_bank=pass ? b_new:b_old;
 wire selected_enable=pass ? en_new:en_old;
 wire signed [17:0] cq=!selected_enable ? (center ? 18'sd65536:18'sd0):
                                      coefficient_q;
 wire [9:0] coefficient_read_address={1'b0,tap}+
   (selected_bank==0 ? 10'd0:selected_bank==1 ? 10'd321:10'd642);
 wire [9:0] coefficient_write_address={1'b0,coef_addr}+
   (coef_bank==0 ? 10'd0:coef_bank==1 ? 10'd321:10'd642);
 assign ready=!busy;
 // No reset on memory: valid-depth masks prevent reads of uninitialized data.
 always @(posedge clk) begin
   if(accept && rst_n)begin hist_a[write_ptr]<=in_data;hist_b[write_ptr]<=in_data;end
   if(state==1)begin
     ha<=hist_a[ra];hb<=hist_b[rb];coefficient_q<=coefficient_store[coefficient_read_address];
   end
   // Padding addresses in the old 512-word banks were never read by this
   // 641-tap core. Ignore them here so they cannot alias another logical bank.
   if(coef_we && coef_bank<3 && coef_addr<=320)
     coefficient_store[coefficient_write_address]<=coef_data;
 end
 reg [49:0] magnitude,rounded;reg [20:0] remainder;
 always @* begin
   magnitude=round_input[49] ? -round_input:round_input;
   rounded=magnitude>>21;remainder=magnitude[20:0];
   if(remainder>21'h100000 || (remainder==21'h100000 && rounded[0])) rounded=rounded+1'b1;
 end
 always @(posedge clk)begin
   out_valid<=0;
   if(!rst_n)begin
     busy<=0;state<=0;write_ptr<=0;current_ptr<=0;filled<=0;tap<=0;pass<=0;
     acc<=0;acc_old<=0;product_valid<=0;out_data<=0;out_seq<=0;
     dropped<=0;saturated<=0;max_cycles<=0;cycles<=0;
     b_old<=0;b_new<=0;en_old<=0;en_new<=0;do_fade<=0;a_weight<=0;
     seq_hold<=0;mask_a<=0;mask_b<=0;center<=0;blend<=0;delta<=0;round_input<=0;blend_bit<=0;
   end else begin
     if(in_valid && busy)dropped<=dropped+1'b1;
     if(busy)cycles<=cycles+1'b1;
     case(state)
       0:if(in_valid)begin
         busy<=1;cycles<=1;current_ptr<=write_ptr;write_ptr<=write_ptr+1'b1;
         if(filled<641)filled<=filled+1'b1;
         seq_hold<=in_seq;b_old<=old_bank;b_new<=new_bank;en_old<=old_enable;en_new<=new_enable;
         do_fade<=fading;a_weight<=alpha;tap<=0;pass<=0;acc<=0;product_valid<=0;state<=1;
       end
       1:begin
         mask_a<=({1'b0,tap}<filled);mask_b<=((10'd640-{1'b0,tap})<filled);center<=(tap==320);
         if(product_valid)acc<=acc+{{9{product[34]}},product};
         state<=2;
       end
       2:begin
         product<=pair_value*cq;product_valid<=1;
         if(tap==320)state<=3;else begin tap<=tap+1'b1;state<=1;end
       end
       3:begin acc<=acc+{{9{product[34]}},product};product_valid<=0;state<=4;end
       4:if(do_fade && !pass)begin
         acc_old<=acc;acc<=0;pass<=1;tap<=0;state<=1;
       end else if(do_fade)begin
         blend<={{6{acc_old[43]}},acc_old}<<<5;
         delta<={{6{acc[43]}},acc}-{{6{acc_old[43]}},acc_old};blend_bit<=0;state<=5;
       end else begin round_input<={{6{acc[43]}},acc}<<<5;state<=7;end
       5:begin
         if(a_weight[blend_bit])blend<=blend+(delta<<<blend_bit);
         if(blend_bit==5)state<=6;else blend_bit<=blend_bit+1'b1;
       end
       6:begin round_input<=blend;state<=7;end
       7:begin
         if((!round_input[49] && rounded>32767) || (round_input[49] && rounded>32768))begin
           out_data<=round_input[49] ? -16'sd32768:16'sd32767;saturated<=saturated+1'b1;
         end else out_data<=round_input[49] ? -$signed(rounded[15:0]):$signed(rounded[15:0]);
         out_seq<=seq_hold;out_valid<=1;busy<=0;state<=0;
         if(cycles>max_cycles)max_cycles<=cycles;
       end
       default:state<=0;
     endcase
   end
 end
endmodule
