`timescale 1ns/1ps
// Read-only instrumentation of the released V19 complete automatic core.
// No frequency, target, event or component label enters the DUT.
module tb_full_quality;
 GSR GSR(.GSRI(1'b1));
 reg clk=0;always #10 clk=~clk;
 reg rst_n=0;wire tick;
 sample_tick cadence(clk,rst_n,tick);
 reg[31:0] sent=0;
 integer sample_count=5120,case_id=0,outputs=0,clock_count=0; integer timeout_cycles; string input_path;
 reg signed[15:0] data[0:131071];
 wire iv=rst_n&&tick&&sent<sample_count;
 wire signed[15:0] x=sent<sample_count?data[sent]:16'sd0;
 wire ready,ov;wire signed[15:0] y;wire[31:0] os;
 wire[31:0] dropped,saturated,commits,numeric,deadline,seqfault,errors,frames,detect_cycles;
 wire[15:0] fir_cycles;wire[1:0] waiting;wire running;wire[3:0] active;wire[11:0] groups;
 wire[7:0] error_code;
 pro_v2_auto_core #(.MAX_CENTERS(4)) dut(
  .clk(clk),.rst_n(rst_n),.in_valid(iv),.in_data(x),.in_seq(sent),
  .ready(ready),.out_valid(ov),.out_data(y),.out_seq(os),
  .dropped(dropped),.saturated(saturated),.max_fir_cycles(fir_cycles),
  .commits(commits),.waiting(waiting),.running(running),.active_mask(active),.active_groups(groups),
  .executor_errors(errors),.numeric_faults(numeric),.deadline_faults(deadline),.sequence_faults(seqfault),
  .last_error(error_code),.completed_frames(frames),.max_detection_cycles(detect_cycles));
 integer cfgf; integer authf; integer maximum_aux_cycles=0,maximum_fit_cycles=0; integer maximum_waiting=0;
 integer sf,of,cf,bf,af,ef,b,k,last_input_clock=0,minimum_gap=2147483647,maximum_gap=0;
 integer last_outputs=0,held_cycles=0;
 string folder;
 initial begin
  if(!$value$plusargs("SAMPLES=%d",sample_count))sample_count=5120;
  if(!$value$plusargs("INPUT=%s",input_path))input_path="input.mem";
  if(!$value$plusargs("FOLDER=%s",folder))folder="trace/";
  timeout_cycles=sample_count*1563+500000;
  $readmemh(input_path,data,0,sample_count-1);
  authf=$fopen({folder,"authorizations.csv"},"w");
  $fwrite(authf,"clock,observation,window_end,alive,strong,frequencies_hex,identities_hex,versions_hex,block_new,ambiguity\n");
  cfgf=$fopen({folder,"cfg_snapshots.csv"},"w");
  $fwrite(cfgf,"clock,bank,version,mask,frequencies_hex,identities_hex,versions_hex,alive,strong\n");
  sf=$fopen({folder,"samples.csv"},"w");of=$fopen({folder,"outputs.csv"},"w");
  cf=$fopen({folder,"coefficient_writes.csv"},"w");bf=$fopen({folder,"initial_banks.csv"},"w");
  af=$fopen({folder,"observations.csv"},"w");ef=$fopen({folder,"events.csv"},"w");
  $fwrite(sf,"sample,clock,input,old_bank,new_bank,old_enable,new_enable,alpha,fading,commits,active,waiting,running,proposal_accept,active_version,proposal_version\n");
  $fwrite(of,"sample,clock,output\n");$fwrite(cf,"clock,bank,tap,value\n");
  $fwrite(bf,"bank,tap,value\n");
  $fwrite(af,"clock,observation,window_end,count,strong_bits,grid0,grid1,grid2,grid3,grid4,active,waiting,commits,ambiguity\n");
  $fwrite(ef,"clock,event,bank,version,waiting,active\n");
  repeat(8)@(negedge clk);
  for(b=0;b<3;b=b+1)for(k=0;k<321;k=k+1)
   $fwrite(bf,"%0d,%0d,%0d\n",b,k,$signed(dut.executor.fir.coefficient_store[b*321+k]));
  rst_n=1;
  wait(outputs==sample_count);
  repeat(150000)@(negedge clk);
  $display("METRICS aux_max=%0d fit_max=%0d",maximum_aux_cycles,maximum_fit_cycles);
  $display("SERVICE capacity=4 generation_last=%0d service_max=%0d waiting_max=%0d stale_queue=%0d rejected_jobs=%0d",dut.executor.manager.generation_cycles,dut.executor.manager.max_service_cycles,maximum_waiting,dut.executor.manager.stale_queue,dut.executor.manager.rejected_jobs);
  $display("FINAL case=%0d sent=%0d outputs=%0d obs=%0d commits=%0d max_fir=%0d detect=%0d active=%h waiting=%0d running=%0d dropped=%0d sat=%0d numeric=%0d deadline=%0d seqfault=%0d errors=%0d error_code=%h gap_min=%0d gap_max=%0d",case_id,sent,outputs,frames,commits,fir_cycles,detect_cycles,active,waiting,running,dropped,saturated,numeric,deadline,seqfault,errors,error_code,minimum_gap,maximum_gap);
  if(sent!=sample_count||dropped||saturated||numeric||deadline||seqfault||errors||error_code||fir_cycles>1562)
   $fatal(1,"complete auto core integrity failure");
  if(frames!=(sample_count-512)/64+1)$fatal(1,"main observation count mismatch");

  
  $display("QUEUE_CLOCK waiting_clocks=%0d running_and_waiting_clocks=%0d longest_running_and_waiting=%0d matrix_merge_and_waiting_clocks=%0d new_descriptor_accepts=%0d",queue_waiting_clocks,queue_overlap_clocks,queue_overlap_max,queue_matrix_overlap_clocks,queue_accepts);
  $fclose(queue_trace_file);

$fclose(aux_audit_file);$fclose(psf);$fclose(authf);$fclose(cfgf);$fclose(sf);$fclose(of);$fclose(cf);$fclose(bf);$fclose(af);$fclose(ef);
  $display("PASS FULL_AUTO_QUALITY_TRACE case=%0d",case_id);$finish;
 end
 always @(posedge clk)begin
  clock_count=clock_count+1;
  if(rst_n)begin
   if(dut.aux_done && dut.aux_cycles>maximum_aux_cycles)maximum_aux_cycles=dut.aux_cycles;
   if(dut.fit_done && dut.fit_cycles>maximum_fit_cycles)maximum_fit_cycles=dut.fit_cycles;
   if(waiting>maximum_waiting)maximum_waiting=waiting;
   if(iv)begin
    if(!ready)$fatal(1,"sample deadline missed %0d",sent);
    if(sent>0)begin
     if(clock_count-last_input_clock!=1562&&clock_count-last_input_clock!=1563)$fatal(1,"sample cadence changed");
     if(clock_count-last_input_clock<minimum_gap)minimum_gap=clock_count-last_input_clock;
     if(clock_count-last_input_clock>maximum_gap)maximum_gap=clock_count-last_input_clock;
    end
    last_input_clock=clock_count;
    $fwrite(sf,"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d\n",sent,clock_count,$signed(x),dut.executor.old_bank,dut.executor.new_bank,dut.executor.old_enable,dut.executor.new_enable,dut.executor.alpha,dut.executor.fading,commits,active,waiting,running,dut.executor.proposal_accept,dut.executor.active_version,dut.executor.proposal_version);
    sent<=sent+1'b1;
   end
   if(dut.executor.coef_we&&dut.executor.coef_addr<=320)begin
    $fwrite(cf,"%0d,%0d,%0d,%0d\n",clock_count,dut.executor.coef_bank,dut.executor.coef_addr,$signed(dut.executor.coef_data));
    if(dut.executor.fir.busy&&((dut.executor.fir.en_old&&dut.executor.coef_bank==dut.executor.fir.b_old)||(dut.executor.fir.do_fade&&dut.executor.fir.en_new&&dut.executor.coef_bank==dut.executor.fir.b_new)))
     $fatal(1,"coefficient write into in-flight FIR bank");
   end
   if(dut.executor.proposal_accept)$fwrite(cfgf,"%0d,%0d,%0d,%0d,%h,%h,%h,%0d,%0d\n",clock_count,dut.executor.proposal_bank,dut.executor.proposal_version,dut.executor.manager.snap_mask,dut.executor.manager.snap_freq,dut.executor.manager.snap_ids,dut.executor.manager.snap_versions,dut.executor.manager.alive_mask,dut.executor.manager.strong_mask);
   if(dut.executor.proposal_accept)$fwrite(ef,"%0d,commit,%0d,%0d,%0d,%0d\n",clock_count,dut.executor.proposal_bank,dut.executor.proposal_version,waiting,active);
   if(dut.obs_complete)begin
    $fwrite(authf,"%0d,%0d,%0d,%0d,%0d,%h,%h,%h,%0d,%0d\n",clock_count,dut.obs_seq_hold,dut.end_hold,dut.executor.manager.alive_mask,dut.authorized_strong,dut.authorized_frequencies,dut.authorized_identities,dut.authorized_versions,dut.capacity_block_new,dut.ambiguity_count);
    $fwrite(af,"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d\n",clock_count,dut.obs_seq_hold,dut.end_hold,dut.count,dut.strong_bits,dut.grids[0],dut.grids[1],dut.grids[2],dut.grids[3],dut.grids[4],active,waiting,commits,dut.ambiguity_count);
   end
   if(ov)begin
    if(os!==outputs)$fatal(1,"output sequence mismatch %0d expected%0d",os,outputs);
    $fwrite(of,"%0d,%0d,%0d\n",os,clock_count,$signed(y));outputs=outputs+1;
   end
  end
 end
 always begin
  #5000000;
  $display("PROGRESS ns=%0t case=%0d sent=%0d outputs=%0d obs=%0d commits=%0d active=%h wait=%0d",$time,case_id,sent,outputs,frames,commits,active,waiting);
  $fflush(authf);$fflush(cfgf);$fflush();$fflush(sf);$fflush(of);$fflush(cf);$fflush(af);$fflush(ef);
 end
 always @(posedge clk) if(timeout_cycles>0 && clock_count>timeout_cycles)$fatal(1,"full core timeout");
 integer aux_audit_file; reg[31:0] audit_aux_end=0;
 initial begin
  #1;
  aux_audit_file=$fopen({folder,"aux_history_audit.csv"},"w");
  $fwrite(aux_audit_file,"clock,event,observation,window_end,aux_end,aux_age,track,identity,version,raw_frequency,chosen_frequency,old_center,h0,h1,hc,rounded_center,center_change,aux_selected,aux_index,aux_frequency,best_distance,miss,aux_valid,aux_count,aux_words,authorized_words,authorized_mask\n");
 end
 always @(posedge clk) if(rst_n)begin
  if(dut.aux_start)begin
   $fwrite(aux_audit_file,"%0d,AUX_START,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%h,%h,%0d\n",clock_count,dut.frame_seq,dut.window_end,dut.window_end,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,0,0,dut.authorized_frequencies,dut.authorized_strong);
  end
  if(dut.aux_done)begin
   $fwrite(aux_audit_file,"%0d,AUX_DONE,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%h,%h,%0d\n",clock_count,dut.aux_frame,dut.aux_window,dut.aux_window,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,!dut.aux_invalid,dut.aux_count,dut.aux_build,dut.authorized_frequencies,dut.authorized_strong);
  end
  if(dut.obs_begin && dut.obs_ready)begin
   audit_aux_end=dut.executor.control.aux_window_end;
   $fwrite(aux_audit_file,"%0d,BATCH,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%h,%h,%0d\n",clock_count,dut.obs_seq_hold,dut.end_hold,audit_aux_end,dut.end_hold-audit_aux_end,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,dut.executor.control.aux_snapshot_valid,dut.executor.control.aux_count,dut.executor.control.aux_frequencies,dut.authorized_frequencies,dut.authorized_strong);
  end
  if(dut.executor.control.state==26)begin
   $fwrite(aux_audit_file,"%0d,HISTORY_INPUT,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%h,%h,%0d\n",clock_count,dut.executor.control.batch_seq,dut.executor.control.batch_window_end,audit_aux_end,dut.executor.control.batch_window_end-audit_aux_end,dut.executor.control.bi,dut.executor.control.tid[dut.executor.control.bi],dut.executor.control.tv[dut.executor.control.bi],dut.executor.control.best_frequency,dut.executor.control.chosen_frequency,dut.executor.control.tf[dut.executor.control.bi],dut.executor.control.frequency_a,dut.executor.control.h1[dut.executor.control.bi],dut.executor.control.hc[dut.executor.control.bi],dut.executor.control.rounded_center,0,(dut.executor.control.aux_match_found && dut.executor.control.aux_used[dut.executor.control.aux_best_index]),dut.executor.control.aux_best_index,dut.executor.control.af[dut.executor.control.aux_best_index],dut.executor.control.best_distance,dut.executor.control.miss[dut.executor.control.bi],dut.executor.control.batch_aux_valid,dut.executor.control.ac,0,dut.authorized_frequencies,dut.authorized_strong);
  end
  if(dut.executor.control.state==16)begin
   $fwrite(aux_audit_file,"%0d,APPLY_TRACK,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%h,%h,%0d\n",clock_count,dut.executor.control.batch_seq,dut.executor.control.batch_window_end,audit_aux_end,dut.executor.control.batch_window_end-audit_aux_end,dut.executor.control.bi,dut.executor.control.tid[dut.executor.control.bi],dut.executor.control.tv[dut.executor.control.bi],dut.executor.control.best_frequency,dut.executor.control.chosen_frequency,dut.executor.control.tf[dut.executor.control.bi],dut.executor.control.history_hold,dut.executor.control.h1[dut.executor.control.bi],dut.executor.control.hc[dut.executor.control.bi],dut.executor.control.center_hold,dut.executor.control.center_change,(dut.executor.control.aux_match_found && dut.executor.control.aux_used[dut.executor.control.aux_best_index]),dut.executor.control.aux_best_index,dut.executor.control.af[dut.executor.control.aux_best_index],dut.executor.control.best_distance,dut.executor.control.miss[dut.executor.control.bi],dut.executor.control.batch_aux_valid,dut.executor.control.ac,0,dut.authorized_frequencies,dut.authorized_strong);
  end
 end

 integer queue_trace_file;
 integer queue_clock=0,queue_overlap_clocks=0,queue_matrix_overlap_clocks=0;
 integer queue_waiting_clocks=0,queue_overlap_run=0,queue_overlap_max=0,queue_accepts=0;
 reg[31:0] queue_last_signature=0;reg queue_log_valid=0;
 wire queue_new_accept=dut.executor.manager.queue.request && dut.executor.manager.queue.request_ready && !dut.executor.manager.queue.duplicate;
 wire queue_matrix_busy=dut.executor.manager.state==17;
 wire[31:0] queue_signature={dut.executor.waiting,dut.executor.running,
   dut.executor.manager.gen_busy,queue_matrix_busy,dut.executor.blocked,
   queue_new_accept,dut.executor.manager.worker_start,dut.executor.manager.worker_done,
   dut.executor.proposal_accept,dut.executor.active_mask};
 initial begin
  #1;queue_trace_file=$fopen({folder,"queue_clock_events.csv"},"w");
  $fwrite(queue_trace_file,"clock,waiting,running,generator_busy,matrix_merge_busy,manager_state,new_request_accept,worker_start,worker_done,proposal_accept,active_mask,job_mask,q0_valid,q1_valid,q0_mask,q1_mask,locked,groups_hex,sent\n");
 end
 always @(posedge clk)begin
  queue_clock=queue_clock+1;
  if(rst_n)begin
   if(dut.executor.waiting!=0)queue_waiting_clocks=queue_waiting_clocks+1;
   if(dut.executor.waiting!=0 && dut.executor.running)begin
    queue_overlap_clocks=queue_overlap_clocks+1;queue_overlap_run=queue_overlap_run+1;
    if(queue_overlap_run>queue_overlap_max)queue_overlap_max=queue_overlap_run;
   end else queue_overlap_run=0;
   if(dut.executor.waiting!=0 && queue_matrix_busy)queue_matrix_overlap_clocks=queue_matrix_overlap_clocks+1;
   if(queue_new_accept)queue_accepts=queue_accepts+1;
   if(!queue_log_valid || queue_signature!==queue_last_signature)begin
    $fwrite(queue_trace_file,"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%h,%0d\n",
      queue_clock,dut.executor.waiting,dut.executor.running,dut.executor.manager.gen_busy,
      queue_matrix_busy,dut.executor.manager.state,queue_new_accept,dut.executor.manager.worker_start,
      dut.executor.manager.worker_done,dut.executor.proposal_accept,dut.executor.active_mask,
      dut.executor.manager.queue.job_compact[198+:4],dut.executor.manager.queue.v0,dut.executor.manager.queue.v1,
      dut.executor.manager.queue.q0[198+:4],dut.executor.manager.queue.q1[198+:4],
      dut.executor.blocked,dut.executor.groups,sent);
    queue_last_signature=queue_signature;queue_log_valid=1;
   end
  end
 end


 integer psf;
 reg[405:0] previous_pre=0;
 reg previous_valid=0;
 wire[405:0] current_pre={dut.executor.blocked,dut.executor.alive_mask,dut.executor.strong_mask,
 dut.executor.frequencies,dut.executor.identities,dut.executor.versions,dut.executor.groups,dut.capacity_block_new};
 initial begin
  #1;
  psf=$fopen({folder,"pre_service.csv"},"w");
  $fwrite(psf,"clock,blocked,alive,strong,frequencies_hex,identities_hex,versions_hex,groups_hex,block_new\n");
 end
 always @(negedge clk)begin
  #1;
  if(rst_n && (!previous_valid || current_pre!==previous_pre))begin
   $fwrite(psf,"%0d,%0d,%0d,%0d,%h,%h,%h,%h,%0d\n",clock_count+1,
    dut.executor.blocked,dut.executor.alive_mask,dut.executor.strong_mask,
    dut.executor.frequencies,dut.executor.identities,dut.executor.versions,dut.executor.groups,dut.capacity_block_new);
   previous_pre=current_pre;previous_valid=1;
  end
 end

endmodule
