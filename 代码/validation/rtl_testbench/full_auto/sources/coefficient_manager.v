`timescale 1ns/1ps
// One bounded preparation/merge worker, plus at most two waiting descriptors.
// Prepared contributions survive temporarily inadmissible partial regrouping.
// All prepared legal groups overlay one complete snapshot. Pruning has priority.
module coefficient_manager #(parameter MAX_CENTERS=5,parameter GRID_ALIGNED=0)(input clk,input rst_n,input locked,
 input[MAX_CENTERS-1:0] alive_mask,input[MAX_CENTERS-1:0] strong_mask,input[32*MAX_CENTERS-1:0] frequencies,
 input[32*MAX_CENTERS-1:0] identities,input[32*MAX_CENTERS-1:0] versions,input[3*MAX_CENTERS-1:0] groups,
 input[2:0] occupied,input proposal_accept,
 output proposal_valid,output[1:0] proposal_bank,output[31:0] proposal_version,
 output coef_we,output[1:0] coef_bank,output[8:0] coef_addr,output signed[17:0] coef_data,
 output[1:0] waiting,output running,output[MAX_CENTERS-1:0] active_mask_out,output[3*MAX_CENTERS-1:0] active_groups_out,
 output[31:0] generation_cycles,output reg[31:0] rejected_jobs,output[31:0] stale_queue,
 output reg[31:0] generation_errors,output reg[31:0] merge_errors,
 output reg[31:0] generated_members,output reg[31:0] max_service_cycles,
 input block_new);
 reg[MAX_CENTERS-1:0] active_mask,active_buffer;reg[32*MAX_CENTERS-1:0] active_ids,active_versions,active_freq;
 reg[MAX_CENTERS-1:0] prepared_mask,prepared_buffer,prepared_fresh;reg target_valid;
 reg[32*MAX_CENTERS-1:0] prepared_ids,prepared_versions,prepared_freq;
 reg[3*MAX_CENTERS-1:0] active_groups;reg[2:0] scan_group;reg[MAX_CENTERS-1:0] request_mask;
 reg[97*MAX_CENTERS-1:0] descriptor,next_descriptor;reg needs_request,request;integer a;
 always @*begin
   request_mask=groups[scan_group*MAX_CENTERS+:MAX_CENTERS];
   // Unselected members are ignored by compact(); no wide zero-mask is needed.
   next_descriptor={request_mask,identities,versions,frequencies};
   needs_request=0;
   for(a=0;a<MAX_CENTERS;a=a+1)if(request_mask[a])begin
     if(!prepared_fresh[a] && !block_new)needs_request=1;
   end
 end
 reg[4:0] state;reg worker_done;wire worker_start,job_authorized,request_ready;
 wire[97*MAX_CENTERS-1:0] job;wire[198+MAX_CENTERS-1:0] compact_job;wire[31:0] replacements;
 // Register request/descriptor before scheduler. Duplicate acceptance is a
 // no-op in the scheduler. A stale held request is discarded, never retried.
 reg request_live;integer ri;
 always @*begin
   request_live=!block_new && descriptor[96*MAX_CENTERS+:MAX_CENTERS]!=0 &&
     (descriptor[96*MAX_CENTERS+:MAX_CENTERS]==groups[0+:MAX_CENTERS] || descriptor[96*MAX_CENTERS+:MAX_CENTERS]==groups[MAX_CENTERS+:MAX_CENTERS] ||
      descriptor[96*MAX_CENTERS+:MAX_CENTERS]==groups[2*MAX_CENTERS+:MAX_CENTERS]);
   for(ri=0;ri<MAX_CENTERS;ri=ri+1)if(descriptor[96*MAX_CENTERS+ri])request_live=request_live && strong_mask[ri] &&
     descriptor[64*MAX_CENTERS+ri*32+:32]==identities[ri*32+:32] &&
     descriptor[32*MAX_CENTERS+ri*32+:32]==versions[ri*32+:32];
 end
 always @(posedge clk)begin
   if(!rst_n)begin request<=0;descriptor<=0;end
   else if(locked)request<=0;
   else if(request)begin if(request_ready || !request_live)request<=0;end
   else if(needs_request)begin descriptor<=next_descriptor;request<=1;end
 end
 reg[MAX_CENTERS-1:0] retained;
 wire pruning=target_valid && retained!=active_mask;
 wire merge_needed;reg worker_offer;
 // Reserve one idle cycle before offering the worker. This isolates the wide
 // desired-snapshot comparison from queue register enables. An already offered
 // cycle can accept one authorized task; merge/prune waits until that decision
 // is known, so dispatch and merge can never own the worker simultaneously.
 always @(posedge clk)begin
   if(!rst_n)worker_offer<=0;
   else worker_offer<=state==0 && target_valid && !pruning && !merge_needed && !locked && !running && !worker_start && !block_new;
 end
 config_scheduler #(.MAX_CENTERS(MAX_CENTERS)) queue(clk,rst_n,locked,strong_mask,identities,versions,groups,
   request && !block_new,descriptor,request_ready,worker_offer,worker_done,
   worker_start,running,job,job_authorized,waiting,stale_queue,replacements,compact_job);
 reg job_mode;reg[MAX_CENTERS-1:0] work_buffer;reg[2:0] member;
 // The worker reads the original two tokens directly; expanding to five
 // sparse lanes and dynamically selecting them again only adds mux logic.
 wire[95:0] member_token=member==compact_job[98:96] ? compact_job[95:0]:compact_job[194:99];
 wire repair_cos_start,repair_cos_done;wire[12:0] repair_cos_phase;wire signed[17:0] repair_cos_value;
 reg gen_start;reg[31:0] gen_frequency;wire gen_busy,gen_done,gen_error,gen_valid;
 wire[8:0] gen_addr;wire signed[17:0] gen_value;wire[63:0] normalization;
 // AUTO centers are multiples of2^19 FCW. Re-state the invariant at the
 // arithmetic boundary after descriptor packing/queue registers; generic
 // integrations retain arbitrary-FCW interpolation by default.
 wire[31:0] generator_word=GRID_ALIGNED ? {gen_frequency[31:19],19'd0}:gen_frequency;
 // synthesis translate_off
 always @(posedge clk)if(rst_n && GRID_ALIGNED && gen_start && gen_frequency[18:0]!==19'd0)
   $fatal(1,"GRID_ALIGNED generator received non-grid FCW %h",gen_frequency);
 // synthesis translate_on
 coefficient_generator #(.GRID_ALIGNED(GRID_ALIGNED)) generator(clk,rst_n,gen_start,generator_word,gen_busy,gen_done,
   gen_error,gen_valid,gen_addr,gen_value,generation_cycles,normalization,repair_cos_start,repair_cos_phase,repair_cos_done,repair_cos_value);
 // 2*MAX_CENTERS independent 512-word regions, two contribution slots per track. The
 // currently committed full bank protects old output even after active_buffer
 // changes, so a freed contribution region can be reused during a fade.
 // 1024x12 logical banks, still one physical RAM per track; exact coefficients.
 // Restore all six redundant sign bits at readout; arithmetic remains 18-bit.
 reg[2:0] merge_member;reg[8:0] tap;
 wire[11:0] cache_values[0:MAX_CENTERS-1];
 reg[2:0] cache_track;
 // divide_coefficient_bound10 returns a rounded magnitude <=1024 for
 // valid requests (or an error quotient, which produces no coef_valid).
 // Rounded valid magnitude <=1024, hence signed 12 bits suffice exactly. The removed
 // upper six storage bits are copies of bit 11. FIR output stays Q16/18.
 wire[11:0] selected_cache_value=cache_values[cache_track];
 wire signed[17:0] cache_q={{6{selected_cache_value[11]}},selected_cache_value};

 reg[MAX_CENTERS-1:0] snap_mask,snap_buffer,snap_add_mask;wire[32*MAX_CENTERS-1:0] snap_ids,snap_versions,snap_freq;
 reg[3*MAX_CENTERS-1:0] snap_groups;reg[1:0] build_bank;reg[31:0] bank_version;
 reg repair_start;wire repair_busy,repair_done,repair_error,repair_valid;
 wire[2:0] repair_track;wire[8:0] repair_addr,repair_coef_addr;
 wire signed[17:0] repair_coef;wire[24*MAX_CENTERS-1:0] repair_weights;
 wire[31:0] repair_cycles,repair_tag;
 wire[13*MAX_CENTERS-1:0] repair_centers;
 wire[2:0] cache_read_member=state==17 ? repair_track:merge_member;
 wire[8:0] cache_read_tap=state==17 ? repair_addr:tap;
 genvar repair_member;
 generate for(repair_member=0;repair_member<MAX_CENTERS;repair_member=repair_member+1)begin:repair_word
   assign repair_centers[repair_member*13+:13]=snap_freq[repair_member*32+19+:13];
 end endgenerate
 overlap_backend_q15_mac #(.MAX_CENTERS(MAX_CENTERS)) repair(clk,rst_n,repair_start,snap_mask,repair_centers,
   bank_version,repair_busy,repair_done,repair_error,repair_tag,repair_track,repair_addr,
   cache_q[15:0],repair_valid,repair_coef_addr,repair_coef,repair_weights,repair_cycles,repair_cos_start,repair_cos_phase,repair_cos_done,repair_cos_value);

 // synthesis translate_off
 always @(posedge clk)if(rst_n && state==17 && gen_busy)$fatal(1,"generator/backend ownership conflict");
 always @(posedge clk)if(rst_n && gen_valid && gen_value[17:12]!=={6{gen_value[11]}})
   $fatal(1,"contribution cache sign extension invariant");
 // synthesis translate_on

 wire[3:0] write_region={member,work_buffer[member]};
 wire[3:0] read_region={merge_member,snap_buffer[merge_member]};
 always @(posedge clk)cache_track<=cache_read_member;
 genvar contribution_track;
 generate for(contribution_track=0;contribution_track<MAX_CENTERS;contribution_track=contribution_track+1)begin:contribution_bank
   (* syn_ramstyle="block_ram" *) reg[11:0] values[0:1023];
   reg[11:0] read_value;
   assign cache_values[contribution_track]=read_value;
   always @(posedge clk)begin
     if(gen_valid && member==contribution_track)values[{work_buffer[member],gen_addr}]<=gen_value[11:0];
     read_value<=values[{snap_buffer[cache_read_member],cache_read_tap}];
   end
 end endgenerate
 // Snapshot assessment is a registered background FSM (states 11..16).
 // It never places identity comparison, group compaction and sorting on one
 // timing path. A new observation invalidates the entire assessment and any
 // ready bank; individual group generation still has its own final auth check.
 reg[MAX_CENTERS-1:0] desired_mask,desired_buffer,add_mask;
 wire[32*MAX_CENTERS-1:0] desired_ids,desired_versions,desired_freq;
 reg[3*MAX_CENTERS-1:0] desired_groups,prepared_groups,eval_groups;reg desired_legal;
 reg[MAX_CENTERS-1:0] sort_group[0:5];reg[2:0] sort_round;
 // One backend worker owns both prepared metadata and full-bank construction.
 // During states 4/6/7/8/9 active/prepared token sources cannot change; any
 // observation immediately kills target_valid, and must discard the ready
 // bank before another assessment or generator job can run. Thus the two
 // copied 480-bit token vectors can be represented by their member selectors.
 // Keep a separate snapshot selector so diagnostics/freshness retain the
 // desired-vs-building distinction without two complete register copies.
 genvar token_member;
 wire[MAX_CENTERS-1:0] token_difference;
 generate for(token_member=0;token_member<MAX_CENTERS;token_member=token_member+1)begin:token_selection
   // One comparison shared by desired-vs-active and desired-vs-snapshot.
   // Selecting from the same two values commutes with equality: different
   // selectors matter exactly when the two source tokens are different.
   assign token_difference[token_member]=
     prepared_ids[token_member*32+:32]!=active_ids[token_member*32+:32] ||
     prepared_versions[token_member*32+:32]!=active_versions[token_member*32+:32] ||
     prepared_freq[token_member*32+:32]!=active_freq[token_member*32+:32];
   assign desired_ids[token_member*32+:32]=add_mask[token_member] ?
     prepared_ids[token_member*32+:32]:active_ids[token_member*32+:32];
   assign desired_versions[token_member*32+:32]=add_mask[token_member] ?
     prepared_versions[token_member*32+:32]:active_versions[token_member*32+:32];
   assign desired_freq[token_member*32+:32]=add_mask[token_member] ?
     prepared_freq[token_member*32+:32]:active_freq[token_member*32+:32];
   assign snap_ids[token_member*32+:32]=snap_add_mask[token_member] ?
     prepared_ids[token_member*32+:32]:active_ids[token_member*32+:32];
   assign snap_versions[token_member*32+:32]=snap_add_mask[token_member] ?
     prepared_versions[token_member*32+:32]:active_versions[token_member*32+:32];
   assign snap_freq[token_member*32+:32]=snap_add_mask[token_member] ?
     prepared_freq[token_member*32+:32]:active_freq[token_member*32+:32];
 end endgenerate
 wire eval_pruning=retained!=active_mask;
 wire[MAX_CENTERS-1:0] add0=!block_new && !eval_pruning && eval_groups[0+:MAX_CENTERS]!=0 &&
   (eval_groups[0+:MAX_CENTERS]&prepared_fresh)==eval_groups[0+:MAX_CENTERS] ? eval_groups[0+:MAX_CENTERS] : {MAX_CENTERS{1'b0}};
 wire[MAX_CENTERS-1:0] add1=!block_new && !eval_pruning && eval_groups[MAX_CENTERS+:MAX_CENTERS]!=0 &&
   (eval_groups[MAX_CENTERS+:MAX_CENTERS]&prepared_fresh)==eval_groups[MAX_CENTERS+:MAX_CENTERS] ? eval_groups[MAX_CENTERS+:MAX_CENTERS] : {MAX_CENTERS{1'b0}};
 wire[MAX_CENTERS-1:0] add2=!block_new && !eval_pruning && eval_groups[2*MAX_CENTERS+:MAX_CENTERS]!=0 &&
   (eval_groups[2*MAX_CENTERS+:MAX_CENTERS]&prepared_fresh)==eval_groups[2*MAX_CENTERS+:MAX_CENTERS] ? eval_groups[2*MAX_CENTERS+:MAX_CENTERS] : {MAX_CENTERS{1'b0}};
 function sort_exchange;
   input[MAX_CENTERS-1:0] left,right;
   begin sort_exchange=left==0 || (right!=0 && left>right);end
 endfunction
 assign merge_needed=target_valid && desired_legal && (desired_mask!=active_mask || desired_groups!=active_groups ||
   desired_buffer!=active_buffer || |(add_mask&token_difference));
 wire snap_fresh=target_valid && desired_legal && desired_mask==snap_mask && desired_groups==snap_groups &&
   desired_buffer==snap_buffer && (((add_mask^snap_add_mask)&token_difference)==0);
 wire[1:0] free_bank=!occupied[0]?0:!occupied[1]?1:!occupied[2]?2:3;
 reg signed[20:0] total;wire overflow=(total>21'sd131071 || total< -21'sd131072);
 assign coef_we=state==17 && repair_valid;
 assign coef_bank=build_bank;assign coef_addr=repair_coef_addr;assign coef_data=repair_coef;
 assign proposal_valid=state==9 && target_valid && !locked && snap_fresh && (!block_new || snap_add_mask==0);
 assign proposal_bank=build_bank;assign proposal_version=bank_version;
 assign active_mask_out=active_mask;assign active_groups_out=active_groups;
 reg[31:0] service_cycles;integer si;
 always @(posedge clk)begin
   gen_start<=0;worker_done<=0;repair_start<=0;
   if(!rst_n)begin
     state<=0;scan_group<=0;active_mask<=0;active_buffer<=0;active_ids<=0;active_versions<=0;
     active_freq<=0;active_groups<=0;job_mode<=0;work_buffer<=0;member<=0;gen_start<=0;
     prepared_mask<=0;prepared_buffer<=0;prepared_ids<=0;prepared_versions<=0;prepared_freq<=0;
     prepared_fresh<=0;retained<=0;target_valid<=0;add_mask<=0;desired_mask<=0;desired_buffer<=0;
     desired_groups<=0;desired_legal<=0;
     prepared_groups<=0;eval_groups<=0;sort_round<=0;
     for(si=0;si<6;si=si+1)sort_group[si]<=0;
     gen_frequency<=0;merge_member<=0;tap<=0;snap_mask<=0;snap_buffer<=0;snap_add_mask<=0;
     snap_groups<=0;build_bank<=1;bank_version<=0;total<=0;
     worker_done<=0;rejected_jobs<=0;generation_errors<=0;merge_errors<=0;generated_members<=0;
     service_cycles<=0;max_service_cycles<=0;
   end else begin
     scan_group <= scan_group == 3'd2 ? 3'd0 : scan_group + 3'd1;
     if(state!=0)service_cycles<=service_cycles+1'b1;
     case(state)
       0:begin
         service_cycles<=0;
         if(worker_start)begin
           job_mode<=1;work_buffer<=active_buffer;member<=0;state<=1;
         end else if(!locked && !running && !worker_offer)begin
           if(!target_valid)begin member<=0;state<=11;end
           else if(pruning || merge_needed)begin job_mode<=0;state<=4;end
         end
       end
       1:begin
         if(member==MAX_CENTERS)state<=3;
         else if(!job[96*MAX_CENTERS+member])member<=member+1'b1;
         else if(prepared_mask[member] && prepared_ids[member*32+:32]==member_token[95:64] &&
                 prepared_versions[member*32+:32]==member_token[63:32] &&
                 prepared_freq[member*32+:32]==member_token[31:0])begin
           work_buffer[member]<=prepared_buffer[member];member<=member+1'b1;
         end else begin
           work_buffer[member]<=~active_buffer[member];gen_frequency<=member_token[31:0];
           prepared_mask[member]<=0;target_valid<=0;gen_start<=1;state<=2;
         end
       end
       2:if(gen_done)begin
         if(gen_error)begin generation_errors<=generation_errors+1'b1;state<=10;end
         else begin generated_members<=generated_members+1'b1;member<=member+1'b1;state<=1;end
       end
       3:if(!locked)begin
         target_valid<=0;
         if(job_authorized && !block_new)begin
           for(si=0;si<MAX_CENTERS;si=si+1)if(job[96*MAX_CENTERS+si])begin
             prepared_mask[si]<=1;prepared_buffer[si]<=work_buffer[si];
             prepared_ids[si*32+:32]<=job[64*MAX_CENTERS+si*32+:32];
             prepared_versions[si*32+:32]<=job[32*MAX_CENTERS+si*32+:32];
             prepared_freq[si*32+:32]<=job[si*32+:32];
           end
         end else rejected_jobs<=rejected_jobs+1'b1;
         state<=10;
       end
       4:if(!locked)begin
         if(!target_valid || !desired_legal || !merge_needed)state<=10;
         else if(free_bank!=3)begin
           snap_mask<=desired_mask;snap_buffer<=desired_buffer;snap_add_mask<=add_mask;snap_groups<=desired_groups;
           build_bank<=free_bank;tap<=0;merge_member<=0;total<=0;repair_start<=1;state<=17;
         end
       end
       6:state<=7; // synchronous contribution RAM read latency
       7:begin
         if(snap_mask[merge_member])total<=total-{{3{cache_q[17]}},cache_q};
         if(merge_member==MAX_CENTERS-1)state<=8;else begin merge_member<=merge_member+1'b1;state<=6;end
       end
       8:begin
         if(overflow)begin merge_errors<=merge_errors+1'b1;state<=10;end
         else if(tap==320)begin bank_version<=bank_version+1'b1;state<=9;end
         else begin tap<=tap+1'b1;merge_member<=0;total<=tap==319?21'sd65536:21'sd0;state<=6;end
       end
       17:if(repair_done)begin
         if(repair_error)begin merge_errors<=merge_errors+1'b1;state<=10;end
         else begin bank_version<=bank_version+1'b1;state<=9;end
       end
       9:begin
         if(!target_valid)begin rejected_jobs<=rejected_jobs+1'b1;state<=10;end
         else if(!locked)begin
           if(!snap_fresh)begin rejected_jobs<=rejected_jobs+1'b1;state<=10;end
           else if(proposal_accept)begin
             active_mask<=snap_mask;active_buffer<=snap_buffer;active_ids<=snap_ids;
             active_versions<=snap_versions;active_freq<=snap_freq;active_groups<=snap_groups;
             target_valid<=0;state<=10;
           end
         end
       end
       10:begin
         if(service_cycles>max_service_cycles)max_service_cycles<=service_cycles;
         if(job_mode)worker_done<=1;state<=0;
       end
       11:begin
         // Serialize the MAX_CENTERS assessments through the same member read mux
         // used by generation-cache lookup. Authority inputs are stable while
         // unlocked; a new observation aborts this whole assessment below.
         retained[member]<=active_mask[member] && alive_mask[member] &&
           active_ids[member*32+:32]==identities[member*32+:32];
         prepared_fresh[member]<=prepared_mask[member] && alive_mask[member] && strong_mask[member] &&
           prepared_ids[member*32+:32]==identities[member*32+:32] &&
           prepared_versions[member*32+:32]==versions[member*32+:32] &&
           prepared_freq[member*32+:32]==frequencies[member*32+:32];
         if(member==MAX_CENTERS-1)begin eval_groups<=groups;state<=12;end
         else member<=member+1'b1;
       end
       12:begin add_mask<=add0|add1|add2;prepared_groups<={add2,add1,add0};state<=13;end
       13:begin
         desired_mask<=retained|add_mask;desired_buffer<=active_buffer;
         for(si=0;si<MAX_CENTERS;si=si+1)if(add_mask[si])begin
           desired_buffer[si]<=prepared_buffer[si];
         end
         for(si=0;si<3;si=si+1)begin
           sort_group[si]<=active_groups[si*MAX_CENTERS+:MAX_CENTERS]&retained&~add_mask;
           sort_group[si+3]<=prepared_groups[si*MAX_CENTERS+:MAX_CENTERS];
         end
         sort_round<=0;state<=14;
       end
       14:begin
         // One odd/even sorting round per clock, disjoint pairs in parallel.
         if(!sort_round[0])begin
           for(si=0;si<3;si=si+1)if(sort_exchange(sort_group[si*2],sort_group[si*2+1]))begin
             sort_group[si*2]<=sort_group[si*2+1];sort_group[si*2+1]<=sort_group[si*2];
           end
         end else begin
           for(si=0;si<2;si=si+1)if(sort_exchange(sort_group[si*2+1],sort_group[si*2+2]))begin
             sort_group[si*2+1]<=sort_group[si*2+2];sort_group[si*2+2]<=sort_group[si*2+1];
           end
         end
         if(sort_round==3'd5)state<=15;else sort_round<=sort_round+3'd1;
       end
       15:begin desired_groups<={sort_group[2],sort_group[1],sort_group[0]};desired_legal<=sort_group[3]==0;state<=16;end
       16:begin target_valid<=1;state<=0;end
       default:state<=0;
     endcase
     // Highest priority: observations cannot authorize any partially assessed or
     // ready configuration. RAM generation may continue but cannot grant rights.
     if(locked || gen_valid || (block_new && |add_mask))target_valid<=0;
     if(locked && state>=11 && state<=16)state<=0;
   end
 end
endmodule

