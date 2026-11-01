`timescale 1ns/1ps
// Only mixed samples enter detection. No source labels, clean reference or PC
// result has a route to the observation/authorization interfaces.
module pro_v2_auto_core #(parameter ALLOW_FFT_OVERLAP=1,parameter MAX_CENTERS=5)(input clk,input rst_n,input in_valid,input signed[15:0] in_data,
 input[31:0] in_seq,output ready,output out_valid,output signed[15:0] out_data,output[31:0] out_seq,
 output[31:0] dropped,saturated,output[15:0] max_fir_cycles,
 output[31:0] commits,generation_cycles,output[1:0] waiting,output running,
 output[MAX_CENTERS-1:0] active_mask,output[3*MAX_CENTERS-1:0] active_groups,output[31:0] executor_errors,
 output[31:0] deadline_faults,sequence_faults,output reg[31:0] numeric_faults,
 output reg[7:0] last_error,output reg[31:0] completed_frames,max_detection_cycles,
 output[31:0] fit_cycles,aux_cycles,output[31:0] fault_events,aux_rejected,
 output[32*MAX_CENTERS-1:0] authorized_frequencies,authorized_identities,authorized_versions,
 output[MAX_CENTERS-1:0] authorized_strong,output[31:0] ambiguity_count,
 output[31:0] capacity_overflow_batches,output capacity_block_new,output fit_capacity_reached,
 output reg[4:0] detected_strong,output reg[159:0] detected_frequencies,
 output reg[2:0] detected_count,output reg[31:0] latest_window_end,
 output reg[16:0] display_cycles,output reg display_time_overflow,
 output reg[3*MAX_CENTERS-1:0] display_groups,output display_fading,output reg display_ambiguous,
 output[15:0] trials,output[7:0] searches,refits,output[15:0] rhs_count);
 wire mv,md,av,ad,front_abort,front_busy;wire[8:0] mb;wire[9:0] ab;
 wire signed[31:0] mr,mi;wire[63:0] mp,ap;
 wire[31:0] frame_seq,window_end,window_clock,main_clock,fault_window,fault_clock,front_cycles;
 reg[3:0] state;reg numeric_abort,aux_start;wire engine_reset=rst_n && !front_abort && !numeric_abort;
 // Frontend done accompanies its last output bin. Auxiliary start must see
 // the completed-load flag registered on that bin's clock.
 always @(posedge clk)begin
   if(!engine_reset)aux_start<=0;else aux_start<=ad;
 end
 wire fit_busy,fit_done,fit_error,fit_valid;wire[7:0] fit_error_code;
 wire[31:0] fit_frame;wire[10:0] fit_grid;wire[79:0] fit_power;
 wire aux_busy,aux_done,aux_invalid,aux_cand_valid;wire[3:0] aux_error_code;
 wire[2:0] aux_rank,aux_count;wire[10:0] aux_grid;wire[31:0] aux_fcw,aux_amplitude,aux_frame,aux_window,aux_rejected_loads;
 wire[63:0] aux_candidate_power;
 reg[159:0] aux_build,aux_cache;reg aux_cache_valid;reg[2:0] aux_cache_count;reg[31:0] aux_cache_end;
 reg[31:0] clock_count,obs_seq_hold,end_hold,clock_hold;reg aux_due;
 reg[2:0] count,gate_index,send_index;reg[10:0] grids[0:4];reg[31:0] powers[0:4];reg[4:0] strong_bits;
 reg candidate_overflow;reg[4:0] diagnostic_bits;reg frame_extra_strong;wire fit_diagnostic;
 wire[46:0] fitted_power_rounded={1'b0,fit_power[79:34]}+
   (fit_power[33:0]>34'h200000000 || (fit_power[33:0]==34'h200000000 && fit_power[34]));
 wire gate_start=state==2;wire gate_busy,gate_done,gate_pass,gate_valid;
 wire[8:0] gate_address;reg[63:0] gate_data;
 wire[64:0] gate_background;wire[63:0] gate_peak;wire[15:0] gate_cycles;
 wire[8:0] native_round={2'd0,grids[gate_index][10:4]}+
   (grids[gate_index][3:0]>8 || (grids[gate_index][3:0]==8 && grids[gate_index][4]));
 reg[63:0] raw_power[0:511] /* synthesis syn_ramstyle="block_ram" */;
 always @(posedge clk)begin
   if(mv)raw_power[mb]<=mp;
   gate_data<=raw_power[gate_address];
 end
 wire[3*MAX_CENTERS-1:0] pending_display_groups;wire pending_display_ambiguous;
 wire[17:0] display_elapsed=clock_count[17:0]-clock_hold[17:0];

 wire obs_ready,obs_complete;
 // synthesis translate_off
 always @(posedge clk)if(rst_n && state==8 && obs_complete && (clock_count-clock_hold)>=262144)
   $fatal(1,"display timestamp range exceeded; do not show modulo duration");
 // synthesis translate_on
 wire[31:0] completed_window;
 wire obs_begin=state==5 && obs_ready;
 wire cand_valid=state==6 && send_index<count;
 wire obs_end=state==7;
 reg observation_fault;
 reg[31:0] fault_end_hold;
 wire[31:0] controller_window=observation_fault ? fault_end_hold:end_hold;
 analysis_frontend #(.ALLOW_FFT_OVERLAP(ALLOW_FFT_OVERLAP)) frontend(.clk(clk),.rst_n(rst_n),.in_valid(in_valid && ready),.in_data(in_data),.in_seq(in_seq),
  .detector_busy(state!=0 || fit_busy || gate_busy || aux_busy),.main_valid(mv),.main_bin(mb),
  .main_real(mr),.main_imag(mi),.main_power(mp),.main_done(md),.aux_valid(av),.aux_bin(ab),.aux_power(ap),.aux_done(ad),
  .frame_seq(frame_seq),.window_end_sample(window_end),.window_end_clock(window_clock),.main_end_clock(main_clock),
  .fault_window_end(fault_window),.fault_clock(fault_clock),.abort(front_abort),.busy(front_busy),
  .deadline_faults(deadline_faults),.sequence_faults(sequence_faults),.max_front_cycles(front_cycles));
 orthogonal_fitter #(.MAX_CENTERS(MAX_CENTERS)) fitter(.clk(clk),.rst_n(engine_reset),.fft_valid(mv),.fft_bin(mb),.fft_real(mr),.fft_imag(mi),
  .start(md),.frame_seq(frame_seq),.busy(fit_busy),.done(fit_done),.error(fit_error),.error_code(fit_error_code),
  .last_cycles(fit_cycles),.result_frame_seq(fit_frame),.out_valid(fit_valid),.grid_abs(fit_grid),.amplitude_square(fit_power),
  .trial_count(trials),.search_count(searches),.refit_count(refits),.rhs_count(rhs_count),.capacity_reached(fit_capacity_reached),.overflow_candidate(fit_diagnostic));
 strong_gate gate(.clk(clk),.rst_n(engine_reset),.start(gate_start),.numeric_valid(1'b1),.peak_bin(native_round[8:0]),
  .power_addr(gate_address),.power_data(gate_data),.busy(gate_busy),.done(gate_done),.strong_pass(gate_pass),
  .result_valid(gate_valid),.background_twice(gate_background),.peak_power(gate_peak),.last_cycles(gate_cycles));
 aux_peak_tracker #(.MAX_CENTERS(MAX_CENTERS)) auxiliary(.clk(clk),.rst_n(engine_reset),.load_valid(av),.load_bin(ab),.load_power(ap),
  .authorized_mask(authorized_strong),.authorized_frequencies(authorized_frequencies),
  .start(aux_start),.frame_seq(frame_seq),.window_end(window_end),.busy(aux_busy),.done(aux_done),.cache_invalid(aux_invalid),
  .error_code(aux_error_code),.cycles(aux_cycles),.rejected_loads(aux_rejected_loads),.cand_valid(aux_cand_valid),
  .cand_rank(aux_rank),.cand_grid(aux_grid),.cand_fcw(aux_fcw),.cand_amplitude(aux_amplitude),.cand_power(aux_candidate_power),
  .out_frame_seq(aux_frame),.out_window_end(aux_window),.cand_count(aux_count));
 dynamic_executor_v14 #(.GRID_ALIGNED(1),.MAX_CENTERS(MAX_CENTERS),.OBS_CENTERS(5)) executor(.clk(clk),.rst_n(rst_n),.in_valid(in_valid),.in_data(in_data),.in_seq(in_seq),
  .ready(ready),.out_valid(out_valid),.out_data(out_data),.out_seq(out_seq),
  .obs_begin(obs_begin),.obs_seq(obs_seq_hold),.cand_valid(cand_valid),.cand_fcw({2'd0,grids[send_index],19'b0}),
  .cand_strong(strong_bits[send_index]),.cand_power(powers[send_index]),.obs_end(obs_end),.obs_ready(obs_ready),
  .cand_diagnostic(diagnostic_bits[send_index]),.obs_block_new(frame_extra_strong),
  .dropped(dropped),.saturated(saturated),.max_cycles(max_fir_cycles),.commits(commits),.generation_cycles(generation_cycles),
  .waiting(waiting),.running(running),.active_mask(active_mask),.active_groups(active_groups),.errors(executor_errors),
  .obs_fault(observation_fault),.window_end_sample(controller_window),.aux_valid(aux_cache_valid),.aux_window_end(aux_cache_end),
  .aux_count(aux_cache_count),.aux_frequencies(aux_cache),.obs_complete(obs_complete),.completed_window_end(completed_window),
  .fault_events(fault_events),.aux_rejected(aux_rejected),.authorized_frequencies(authorized_frequencies),
  .authorized_identities(authorized_identities),.authorized_versions(authorized_versions),.authorized_strong(authorized_strong),
  .ambiguity_count(ambiguity_count),.capacity_overflow_batches(capacity_overflow_batches),.capacity_block_new(capacity_block_new),.display_groups(pending_display_groups),.display_fading(display_fading),.display_ambiguous(pending_display_ambiguous));
 integer i;
 task frame_fail;
 input[7:0] code;
 begin numeric_faults<=numeric_faults+1'b1;last_error<=code;numeric_abort<=1;
   observation_fault<=1;fault_end_hold<=end_hold;state<=0;aux_cache_valid<=0;end
 endtask
 always @(posedge clk)begin
   numeric_abort<=0;observation_fault<=0;
   if(!rst_n)begin
     display_cycles<=0;display_time_overflow<=0;display_groups<=0;display_ambiguous<=0;
     state<=0;numeric_abort<=0;observation_fault<=0;fault_end_hold<=0;clock_count<=0;
     obs_seq_hold<=0;end_hold<=0;clock_hold<=0;aux_due<=0;count<=0;gate_index<=0;send_index<=0;
     strong_bits<=0;diagnostic_bits<=0;frame_extra_strong<=0;candidate_overflow<=0;numeric_faults<=0;last_error<=0;completed_frames<=0;max_detection_cycles<=0;
     aux_build<=0;aux_cache<=0;aux_cache_valid<=0;aux_cache_count<=0;aux_cache_end<=0;
     detected_strong<=0;detected_frequencies<=0;detected_count<=0;latest_window_end<=0;
     for(i=0;i<5;i=i+1)begin grids[i]<=0;powers[i]<=0;end
   end else begin
     clock_count<=clock_count+1'b1;
     if(md)begin
       state<=1;obs_seq_hold<=frame_seq;end_hold<=window_end;clock_hold<=window_clock;
       aux_due<=window_end>=1024 && window_end[7:0]==0;
       count<=0;strong_bits<=0;diagnostic_bits<=0;frame_extra_strong<=0;candidate_overflow<=0;
     end
     if(ad)aux_build<=0;
     if(aux_cand_valid && aux_rank<5)aux_build[aux_rank*32+:32]<=aux_fcw;
     if(aux_done)begin
       aux_cache<=aux_build;aux_cache_count<=aux_count;aux_cache_end<=aux_window;aux_cache_valid<=!aux_invalid;
     end
     if(fit_valid && state==1)begin
       if(count>=5 || fitted_power_rounded[46:32]!=0)candidate_overflow<=1;
       else begin grids[count]<=fit_grid;powers[count]<=fitted_power_rounded[31:0];diagnostic_bits[count]<=fit_diagnostic;count<=count+1'b1;end
     end
     case(state)
       0:begin end
       1:if(fit_done)begin
         if(fit_error)frame_fail(fit_error_code);
         else if(candidate_overflow || fit_frame!=obs_seq_hold)frame_fail(8'h81);
         else begin gate_index<=0;state<=count==0 ? 4:2;end
       end
       2:state<=3;
       3:if(gate_done)begin
         if(!gate_valid)frame_fail(8'h82);
         else begin
           strong_bits[gate_index]<=gate_pass;
           if(diagnostic_bits[gate_index] && gate_pass)frame_extra_strong<=1;
           if(gate_index+1'b1>=count)state<=4;else begin gate_index<=gate_index+1'b1;state<=2;end
         end
       end
       4:begin
         if(!aux_due || (aux_cache_valid && aux_cache_end==end_hold))state<=5;
         else if(aux_done && aux_invalid && aux_window==end_hold)frame_fail({4'h9,aux_error_code});
       end
       5:if(obs_ready)begin send_index<=0;state<=6;end
       6:if(send_index>=count)state<=7;else send_index<=send_index+1'b1;
       7:state<=8;
       8:if(obs_complete)begin
         display_groups<=pending_display_groups;display_ambiguous<=pending_display_ambiguous;
         display_cycles<=display_elapsed[16:0];display_time_overflow<=display_elapsed[17];
         completed_frames<=completed_frames+1'b1;latest_window_end<=end_hold;
         if(clock_count-clock_hold>max_detection_cycles)max_detection_cycles<=clock_count-clock_hold;
         detected_count<=count;detected_strong<=strong_bits;
         for(i=0;i<5;i=i+1)detected_frequencies[i*32+:32]<=i<count ? {2'd0,grids[i],19'b0}:32'd0;
         state<=0;
       end
       default:frame_fail(8'hff);
     endcase
     // The same-hop auxiliary failure may occur before main fitting finishes.
     if(aux_done && aux_invalid && aux_due && aux_window==end_hold && state!=0)
       frame_fail({4'h9,aux_error_code});
     if(front_abort)begin
       state<=0;observation_fault<=1;fault_end_hold<=fault_window;aux_cache_valid<=0;
       count<=0;strong_bits<=0;diagnostic_bits<=0;frame_extra_strong<=0;last_error<=8'hf0;
     end
   end
 end
endmodule
