`timescale 1ns/1ps
module dynamic_executor_v14 #(parameter GRID_ALIGNED=0, parameter MAX_CENTERS=5, parameter OBS_CENTERS=5)(input clk,input rst_n,input in_valid,input signed[15:0] in_data,
 input[31:0] in_seq,output ready,output out_valid,output signed[15:0] out_data,output[31:0] out_seq,
 input obs_begin,input[31:0] obs_seq,input cand_valid,input[31:0] cand_fcw,
 input cand_strong,input[31:0] cand_power,input obs_end,output obs_ready,
 output[31:0] dropped,output[31:0] saturated,output[15:0] max_cycles,
 output[31:0] commits,output[31:0] generation_cycles,output[1:0] waiting,output running,
 output[MAX_CENTERS-1:0] active_mask,output[3*MAX_CENTERS-1:0] active_groups,output[31:0] errors,
 input obs_fault,input[31:0] window_end_sample,
 input aux_valid,input[31:0] aux_window_end,input[2:0] aux_count,input[159:0] aux_frequencies,
 output obs_complete,output[31:0] completed_window_end,
 output[31:0] fault_events,output[31:0] aux_rejected,
 output[32*MAX_CENTERS-1:0] authorized_frequencies,output[32*MAX_CENTERS-1:0] authorized_identities,
 output[32*MAX_CENTERS-1:0] authorized_versions,output[MAX_CENTERS-1:0] authorized_strong,
 output[31:0] ambiguity_count,output[31:0] capacity_overflow_batches,output capacity_block_new,
 input cand_diagnostic,input obs_block_new,
 output[3*MAX_CENTERS-1:0] display_groups,output display_fading,output display_ambiguous);
 wire locked,obs_done;wire[31:0] completed_obs,invalid_batches,ambiguous_batches;
 wire[MAX_CENTERS-1:0] alive_mask,strong_mask;wire[32*MAX_CENTERS-1:0] frequencies,identities,versions;wire[3*MAX_CENTERS-1:0] groups;
 group_controller_v14 #(.GRID_ALIGNED(GRID_ALIGNED),.MAX_CENTERS(MAX_CENTERS),.OBS_CENTERS(OBS_CENTERS)) control(.clk(clk),.rst_n(rst_n),.obs_begin(obs_begin),.obs_seq(obs_seq),
   .cand_valid(cand_valid),.cand_fcw(cand_fcw),.cand_strong(cand_strong),.cand_power(cand_power),.obs_end(obs_end),
   .obs_ready(obs_ready),.locked(locked),.obs_done(obs_done),.completed_obs(completed_obs),
   .alive_mask(alive_mask),.strong_mask(strong_mask),.frequencies(frequencies),.identities(identities),.versions(versions),
   .groups(groups),.invalid_batches(invalid_batches),.ambiguous_batches(ambiguous_batches),
   .obs_fault(obs_fault),.window_end_sample(window_end_sample),.aux_valid(aux_valid),.aux_window_end(aux_window_end),
   .aux_count(aux_count),.aux_frequencies(aux_frequencies),.completed_window_end(completed_window_end),
   .fault_events(fault_events),.aux_rejected(aux_rejected),.capacity_overflow_batches(capacity_overflow_batches),.capacity_block_new(capacity_block_new),
   .cand_diagnostic(cand_diagnostic),.obs_block_new(obs_block_new),.display_ambiguous(display_ambiguous));
 assign obs_complete=obs_done;
 // Read-only telemetry. Entries are meaningful only under authorized_strong;
 // inactive slot payloads can retain their last value and grant no permission.
 assign authorized_frequencies=frequencies;assign authorized_identities=identities;
 assign authorized_versions=versions;assign authorized_strong=strong_mask;
 assign ambiguity_count=ambiguous_batches;
 // Include begin itself: the old result cannot authorize a commit on the first
 // clock of a new serial batch, before locked has registered high.
 wire blocked=locked || obs_begin || obs_fault;
 wire proposal_valid,proposal_accept,coef_we;wire[1:0] proposal_bank,coef_bank;
 wire[31:0] proposal_version,active_version;wire[8:0] coef_addr;wire signed[17:0] coef_data;
 wire[2:0] occupied;wire[31:0] rejected_jobs,stale_queue,generation_errors,merge_errors,generated_members,max_service;
 coefficient_manager #(.MAX_CENTERS(MAX_CENTERS),.GRID_ALIGNED(GRID_ALIGNED)) manager(clk,rst_n,blocked,alive_mask,strong_mask,frequencies,identities,versions,groups,
   occupied,proposal_accept,proposal_valid,proposal_bank,proposal_version,coef_we,coef_bank,coef_addr,coef_data,
   waiting,running,active_mask,active_groups,generation_cycles,rejected_jobs,stale_queue,
   generation_errors,merge_errors,generated_members,max_service,capacity_block_new);
 wire[1:0] old_bank,new_bank;wire old_enable,new_enable,fading,busy;wire[5:0] alpha;
 transition_controller transition(clk,rst_n,in_valid&&ready,out_valid,proposal_valid,proposal_bank,
   proposal_accept,old_bank,old_enable,new_bank,new_enable,fading,alpha,occupied,commits,active_version,proposal_version);
 fir_mac_core fir(clk,rst_n,in_valid,in_data,in_seq,ready,old_bank,old_enable,new_bank,new_enable,alpha,fading,
   coef_we,coef_bank,coef_addr,coef_data,out_valid,out_data,out_seq,dropped,saturated,max_cycles,busy);
 assign display_groups=groups;assign display_fading=fading;
 // Same modulo-2^32 telemetry sum, implemented using spare DSP resources.
 // No register, enable or sample/configuration timing changes.
 wire[31:0] status_ab,status_cd;
 status_exact_add32 status_sum0(invalid_batches,generation_errors,status_ab);
 status_exact_add32 status_sum1(merge_errors,fault_events,status_cd);
 status_exact_add32 status_sum2(status_ab,status_cd,errors);
endmodule

module status_exact_add32(input[31:0] a,b,output[31:0] q);
 wire[47:0] sum;
 // Exact modulo-2^32: DSP adds low 26 bits of a to all of b;
 // one six-bit fabric addition restores a[31:26]. No extra registers.
 MULTALU27X18 #(.P_SEL(1'b0),.A_SEL(1'b0),.C_SEL(1'b1),
 .CASI_SEL(1'b0),.ACC_SEL(1'b0),.PRE_LOAD(48'd0)) unit(
 .DOUT(sum),.CASO(),.SOA(),.A({1'b0,a[25:0]}),.B(18'd1),.C({16'd0,b}),.D(26'd0),
 .SIA(27'd0),.CASI(48'd0),.PSEL(1'b0),.ASEL(1'b0),.CSEL(1'b1),
 .CASISEL(1'b0),.PADDSUB(1'b0),.ACCSEL(1'b0),.ADDSUB(2'd0),
 .CLK(2'd0),.CE(2'b11),.RESET(2'd0));
 wire[5:0] high=sum[31:26]+a[31:26];
 assign q={high,sum[25:0]};
endmodule
