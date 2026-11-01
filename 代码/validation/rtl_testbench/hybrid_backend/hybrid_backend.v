
`timescale 1ns/1ps
module hybrid_backend #(parameter MAX_CENTERS=4)(
 input clk,input rst_n,input in_valid,input signed[15:0] in_data,input[31:0] in_seq,
 output ready,output out_valid,output signed[15:0] out_data,output[31:0] out_seq,
 input blocked,input[MAX_CENTERS-1:0] alive_mask,input[MAX_CENTERS-1:0] strong_mask,
 input[32*MAX_CENTERS-1:0] frequencies,input[32*MAX_CENTERS-1:0] identities,input[32*MAX_CENTERS-1:0] versions,
 input[3*MAX_CENTERS-1:0] groups,input capacity_block_new,
 output[31:0] dropped,output[31:0] saturated,output[15:0] max_cycles,
 output[31:0] commits,output[1:0] waiting,output running,
 output[MAX_CENTERS-1:0] active_mask,output[3*MAX_CENTERS-1:0] active_groups);
 wire proposal_valid,proposal_accept,coef_we;wire[1:0] proposal_bank,coef_bank;
 wire[31:0] proposal_version,active_version;wire[8:0] coef_addr;wire signed[17:0] coef_data;
 wire[2:0] occupied;wire[31:0] rejected_jobs,stale_queue,generation_errors,merge_errors,generated_members,max_service,generation_cycles;
 coefficient_manager #(.MAX_CENTERS(MAX_CENTERS),.GRID_ALIGNED(1)) manager(clk,rst_n,blocked,alive_mask,strong_mask,frequencies,identities,versions,groups,
   occupied,proposal_accept,proposal_valid,proposal_bank,proposal_version,coef_we,coef_bank,coef_addr,coef_data,
   waiting,running,active_mask,active_groups,generation_cycles,rejected_jobs,stale_queue,
   generation_errors,merge_errors,generated_members,max_service,capacity_block_new);
 wire[1:0] old_bank,new_bank;wire old_enable,new_enable,fading,busy;wire[5:0] alpha;
 transition_controller transition(clk,rst_n,in_valid&&ready,out_valid,proposal_valid,proposal_bank,
   proposal_accept,old_bank,old_enable,new_bank,new_enable,fading,alpha,occupied,commits,active_version,proposal_version);
 fir_mac_core fir(clk,rst_n,in_valid,in_data,in_seq,ready,old_bank,old_enable,new_bank,new_enable,alpha,fading,
   coef_we,coef_bank,coef_addr,coef_data,out_valid,out_data,out_seq,dropped,saturated,max_cycles,busy);
endmodule
