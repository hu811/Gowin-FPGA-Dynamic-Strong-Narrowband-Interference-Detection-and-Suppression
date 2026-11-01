`timescale 1ns/1ps
// Controlled-coefficient evidence boundary. Same interleaved two-slot per-center
// 12-bit correction cache required by the independent baseline. The merged side
// additionally owns the production three-bank FIR memory and this serial merge.
// Authorization, scheduling, generation and version lifetime policies are not
// implemented here. The caller must freeze all read slots until sample/fade and
// any merge have completed. Do not claim this controlled interface is an online
// replacement for coefficient_manager.
module merged_storage_backend #(parameter K=4,parameter PACKED_CACHE=0)(
 input clk,rst_n,input in_valid,input signed[15:0] in_data,input[31:0] in_seq,
 input[1:0] old_bank,new_bank,input fading,input[5:0] alpha,
 input coef_we,input[2:0] coef_branch,input[8:0] coef_addr,input signed[17:0] coef_data,
 input build_start,input[1:0] build_bank,input[K-1:0] build_mask,build_slots,
 output ready,out_valid,output signed[15:0] out_data,output[31:0] out_seq,
 output[31:0] dropped,saturated,output[15:0] max_cycles,output busy,
 output reg build_busy,build_done,output reg build_error);
 wire signed[11:0] q;
 reg[2:0] state,member;reg[8:0] tap;reg[1:0] bank;
 reg[K-1:0] mask,slots;reg signed[20:0] total;
 wire[2:0] cache_branch=member+(slots[member]?K:0);
 generate if(PACKED_CACHE)begin:packed_cache
   reg signed[11:0] cache[0:8*512-1] /* synthesis syn_ramstyle="block_ram" */;
   reg signed[11:0] read_q;assign q=read_q;
   always @(posedge clk)begin
     if(coef_we)cache[{coef_branch,coef_addr}]<=coef_data[11:0];
     read_q<=cache[{cache_branch,tap}];
   end
 end else begin:production_cache
   // Same four independent 1024x12 memories/read-selection organization as
   // V19 coefficient_manager, not the smaller experimental packed layout.
   wire[11:0] read_values[0:K-1];reg[2:0] read_track;
   always @(posedge clk)read_track<=member;
   assign q=read_values[read_track];
   genvar t;
   for(t=0;t<K;t=t+1)begin:track
     reg[11:0] values[0:1023] /* synthesis syn_ramstyle="block_ram" */;
     reg[11:0] read_q;assign read_values[t]=read_q;
     always @(posedge clk)begin
       if(coef_we&&coef_branch[1:0]==t)values[{coef_branch[2],coef_addr}]<=coef_data[11:0];
       read_q<=values[{slots[member],tap}];
     end
   end
 end endgenerate
 wire overflow=total>21'sd131071||total< -21'sd131072;
 wire we=state==3&&!overflow;
 fir_mac_core fir(clk,rst_n,in_valid,in_data,in_seq,ready,old_bank,1'b1,new_bank,1'b1,alpha,fading,
   we,bank,tap,total[17:0],out_valid,out_data,out_seq,dropped,saturated,max_cycles,busy);
 always @(posedge clk)begin
  build_done<=0;
  if(!rst_n)begin state<=0;member<=0;tap<=0;bank<=0;mask<=0;slots<=0;total<=0;
   build_busy<=0;build_done<=0;build_error<=0;end
  else case(state)
   0:if(build_start)begin
     bank<=build_bank;mask<=build_mask;slots<=build_slots;member<=0;tap<=0;total<=0;
     build_busy<=1;build_error<=0;state<=1;
   end
   1:state<=2;
   2:begin
     if(mask[member])total<=total-{{9{q[11]}},q};
     if(member==K-1)state<=3;else begin member<=member+1'b1;state<=1;end
   end
   3:if(overflow)begin build_error<=1;build_busy<=0;build_done<=1;state<=0;end
     else if(tap==320)begin build_busy<=0;build_done<=1;state<=0;end
     else begin tap<=tap+1'b1;member<=0;total<=tap==319?21'sd65536:21'sd0;state<=1;end
  endcase
 end
endmodule
