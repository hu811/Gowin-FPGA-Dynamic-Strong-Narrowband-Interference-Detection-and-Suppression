
`timescale 1ns/1ps
module tb_full_quality;
 GSR GSR(.GSRI(1'b1));
 reg clk=0;always #10 clk=~clk;
 reg rst_n=0;wire tick;sample_tick cadence(clk,rst_n,tick);
 reg[31:0] sent=0;integer sample_count=5120,outputs=0,clock_count=0;integer timeout_cycles;
 string input_path,folder,pre_path;
 reg signed[15:0] data[0:131071];
 wire iv=rst_n&&tick&&sent<sample_count;
 wire signed[15:0] x=sent<sample_count?data[sent]:16'sd0;
 wire ready,ov;wire signed[15:0] y;wire[31:0] os;
 reg blocked=0,block_new=0;reg[3:0] alive=0,fresh=0;reg[127:0] freq=0,ids=0,vers=0;reg[11:0] groups=0;
 wire[31:0] dropped,saturated,commits;wire[15:0] fir_cycles;wire[1:0] waiting;wire running;
 wire[3:0] active;wire[11:0] active_groups;
 hybrid_backend dut(clk,rst_n,iv,x,sent,ready,ov,y,os,blocked,alive,fresh,freq,ids,vers,groups,block_new,
   dropped,saturated,fir_cycles,commits,waiting,running,active,active_groups);
 integer sf,of,cf,bf,ef,cfgf,pf,scan,pre_clock=2147483647;
 integer pb,pa,ps,pbn;reg[127:0] pfr,pid,pv;reg[11:0] pg;
 integer b,k,maximum_waiting=0;
 task read_next;
 begin
  scan=$fscanf(pf,"%d,%d,%d,%d,%h,%h,%h,%h,%d\n",pre_clock,pb,pa,ps,pfr,pid,pv,pg,pbn);
  if(scan!=9)pre_clock=2147483647;
 end endtask
 initial begin
  if(!$value$plusargs("SAMPLES=%d",sample_count))sample_count=5120;
  if(!$value$plusargs("INPUT=%s",input_path))input_path="input.mem";
  if(!$value$plusargs("FOLDER=%s",folder))folder="trace/";
  if(!$value$plusargs("PRE=%s",pre_path))$fatal(1,"PRE required");
  timeout_cycles=sample_count*1563+500000;
  $readmemh(input_path,data,0,sample_count-1);
  pf=$fopen(pre_path,"r");if(!pf)$fatal(1,"cannot open preservice");
  begin string header;scan=$fgets(header,pf);end
  read_next();
  sf=$fopen({folder,"samples.csv"},"w");of=$fopen({folder,"outputs.csv"},"w");
  cf=$fopen({folder,"coefficient_writes.csv"},"w");bf=$fopen({folder,"initial_banks.csv"},"w");
  ef=$fopen({folder,"events.csv"},"w");cfgf=$fopen({folder,"cfg_snapshots.csv"},"w");
  $fwrite(sf,"sample,clock,input,old_bank,new_bank,old_enable,new_enable,alpha,fading,commits,active,waiting,running,proposal_accept,active_version,proposal_version\n");
  $fwrite(of,"sample,clock,output\n");$fwrite(cf,"clock,bank,tap,value\n");$fwrite(bf,"bank,tap,value\n");
  $fwrite(ef,"clock,event,bank,version,waiting,active\n");
  $fwrite(cfgf,"clock,bank,version,mask,frequencies_hex,identities_hex,versions_hex,alive,strong\n");
  repeat(8)@(negedge clk);
  for(b=0;b<3;b=b+1)for(k=0;k<321;k=k+1)$fwrite(bf,"%0d,%0d,%0d\n",b,k,$signed(dut.fir.coefficient_store[b*321+k]));
  rst_n=1;
  wait(outputs==sample_count);repeat(150000)@(negedge clk);
  $display("HYBRID FINAL samples=%0d outputs=%0d commits=%0d fir_max=%0d service_max=%0d waiting_max=%0d dropped=%0d saturated=%0d generation_errors=%0d merge_errors=%0d",sent,outputs,commits,fir_cycles,dut.manager.max_service_cycles,maximum_waiting,dropped,saturated,dut.generation_errors,dut.merge_errors);
  if(dropped||saturated||dut.generation_errors||dut.merge_errors||fir_cycles>1562)$fatal(1,"backend integrity");
  
  $display("QUEUE_CLOCK waiting_clocks=%0d running_and_waiting_clocks=%0d longest_running_and_waiting=%0d matrix_merge_and_waiting_clocks=%0d new_descriptor_accepts=%0d",queue_waiting_clocks,queue_overlap_clocks,queue_overlap_max,queue_matrix_overlap_clocks,queue_accepts);
  $fclose(queue_trace_file);

$fclose(sf);$fclose(of);$fclose(cf);$fclose(bf);$fclose(ef);$fclose(cfgf);$fclose(pf);
  $display("PASS HYBRID_BACKEND_TRACE");$finish;
 end
 always @(negedge clk)begin
  #1;
  if(rst_n && pre_clock==clock_count+1)begin
   blocked=pb;alive=pa;fresh=ps;freq=pfr;ids=pid;vers=pv;groups=pg;block_new=pbn;
   read_next();
  end
  if(rst_n && pre_clock<clock_count+1)$fatal(1,"late pre-service input");
 end
 always @(posedge clk)begin
  clock_count=clock_count+1;
  if(rst_n)begin
   if(waiting>maximum_waiting)maximum_waiting=waiting;
   if(iv)begin
    if(!ready)$fatal(1,"sample deadline");
    $fwrite(sf,"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d\n",sent,clock_count,$signed(x),dut.old_bank,dut.new_bank,dut.old_enable,dut.new_enable,dut.alpha,dut.fading,commits,active,waiting,running,dut.proposal_accept,dut.active_version,dut.proposal_version);
    sent<=sent+1'b1;
   end
   if(dut.coef_we&&dut.coef_addr<=320)$fwrite(cf,"%0d,%0d,%0d,%0d\n",clock_count,dut.coef_bank,dut.coef_addr,$signed(dut.coef_data));
   if(dut.proposal_accept)begin
    $fwrite(cfgf,"%0d,%0d,%0d,%0d,%h,%h,%h,%0d,%0d\n",clock_count,dut.proposal_bank,dut.proposal_version,dut.manager.snap_mask,dut.manager.snap_freq,dut.manager.snap_ids,dut.manager.snap_versions,alive,fresh);
    $fwrite(ef,"%0d,commit,%0d,%0d,%0d,%0d\n",clock_count,dut.proposal_bank,dut.proposal_version,waiting,active);
   end
   if(ov)begin
    if(os!==outputs)$fatal(1,"output sequence");
    $fwrite(of,"%0d,%0d,%0d\n",os,clock_count,$signed(y));outputs=outputs+1;
   end
  end
 end
 always @(posedge clk)if(timeout_cycles>0&&clock_count>timeout_cycles)$fatal(1,"timeout");

 integer queue_trace_file;
 integer queue_clock=0,queue_overlap_clocks=0,queue_matrix_overlap_clocks=0;
 integer queue_waiting_clocks=0,queue_overlap_run=0,queue_overlap_max=0,queue_accepts=0;
 reg[31:0] queue_last_signature=0;reg queue_log_valid=0;
 wire queue_new_accept=dut.manager.queue.request && dut.manager.queue.request_ready && !dut.manager.queue.duplicate;
 wire queue_matrix_busy=dut.manager.state==17;
 wire[31:0] queue_signature={dut.waiting,dut.running,
   dut.manager.gen_busy,queue_matrix_busy,dut.blocked,
   queue_new_accept,dut.manager.worker_start,dut.manager.worker_done,
   dut.proposal_accept,dut.active_mask};
 initial begin
  #1;queue_trace_file=$fopen({folder,"queue_clock_events.csv"},"w");
  $fwrite(queue_trace_file,"clock,waiting,running,generator_busy,matrix_merge_busy,manager_state,new_request_accept,worker_start,worker_done,proposal_accept,active_mask,job_mask,q0_valid,q1_valid,q0_mask,q1_mask,locked,groups_hex,sent\n");
 end
 always @(posedge clk)begin
  queue_clock=queue_clock+1;
  if(rst_n)begin
   if(dut.waiting!=0)queue_waiting_clocks=queue_waiting_clocks+1;
   if(dut.waiting!=0 && dut.running)begin
    queue_overlap_clocks=queue_overlap_clocks+1;queue_overlap_run=queue_overlap_run+1;
    if(queue_overlap_run>queue_overlap_max)queue_overlap_max=queue_overlap_run;
   end else queue_overlap_run=0;
   if(dut.waiting!=0 && queue_matrix_busy)queue_matrix_overlap_clocks=queue_matrix_overlap_clocks+1;
   if(queue_new_accept)queue_accepts=queue_accepts+1;
   if(!queue_log_valid || queue_signature!==queue_last_signature)begin
    $fwrite(queue_trace_file,"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%h,%0d\n",
      queue_clock,dut.waiting,dut.running,dut.manager.gen_busy,
      queue_matrix_busy,dut.manager.state,queue_new_accept,dut.manager.worker_start,
      dut.manager.worker_done,dut.proposal_accept,dut.active_mask,
      dut.manager.queue.job_compact[198+:4],dut.manager.queue.v0,dut.manager.queue.v1,
      dut.manager.queue.q0[198+:4],dut.manager.queue.q1[198+:4],
      dut.blocked,dut.groups,sent);
    queue_last_signature=queue_signature;queue_log_valid=1;
   end
  end
 end

endmodule
