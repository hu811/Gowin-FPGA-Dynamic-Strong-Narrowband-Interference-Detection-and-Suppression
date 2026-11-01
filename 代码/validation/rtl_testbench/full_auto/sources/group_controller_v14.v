`timescale 1ns/1ps
// Atomic observation batches; exact inherited 70Hz greedy matching, three-fresh
// history median, 3.90625Hz center grid/deadband, 750Hz connected groups.
// Input fresh flag belongs to THIS raw-input observation; no local detector is
// claimed here. Upstream TEST_REPLAY is explicitly separate from AUTO_DETECT.
module group_controller_v14 #(parameter GRID_ALIGNED=0, parameter MAX_CENTERS=5, parameter OBS_CENTERS=5)(input clk,input rst_n,
 input obs_begin,input[31:0] obs_seq,input cand_valid,input[31:0] cand_fcw,
 input cand_strong,input[31:0] cand_power,input obs_end,
 output obs_ready,output reg locked,output reg obs_done,output reg[31:0] completed_obs,
 output[MAX_CENTERS-1:0] alive_mask,output[MAX_CENTERS-1:0] strong_mask,output[32*MAX_CENTERS-1:0] frequencies,
 output[32*MAX_CENTERS-1:0] identities,output[32*MAX_CENTERS-1:0] versions,output[3*MAX_CENTERS-1:0] groups,
 output reg[31:0] invalid_batches,output reg[31:0] ambiguous_batches,
 input obs_fault,input[31:0] window_end_sample,
 input aux_valid,input[31:0] aux_window_end,input[2:0] aux_count,input[159:0] aux_frequencies,
 output reg[31:0] completed_window_end,output reg[31:0] fault_events,output reg[31:0] aux_rejected,
 output reg[31:0] capacity_overflow_batches,output reg capacity_block_new,
 input cand_diagnostic,input obs_block_new,output display_ambiguous);
 localparam[31:0] MATCH=32'd9395241,GROUP=32'd100663296,GRID=32'd524288;
 localparam[31:0] LONG_MATCH=32'd2097152;
 reg[OBS_CENTERS-1:0] cs,diagnostic;reg[2:0] count;
 reg[31:0] tf[0:MAX_CENTERS-1],tid[0:MAX_CENTERS-1],tv[0:MAX_CENTERS-1],h1[0:MAX_CENTERS-1];
 // Field-major true-dual-port RAM: CF[0..4],CP[8..12],H0[16..20],
 // TP[32..36]. Exported TF/ID/version remain simultaneous register outputs.
 // Each read is scheduled before the state that consumes it; metadata for
 // inactive/uncollected slots is masked, so this RAM needs no reset writes.
 // Physical metadata is two explicit supported normal-mode DPBs. The
 // original port already holds its output on own-port writes; reads remain
 // one synchronous edge. This shadow is simulation-only and never consumed.
 // synthesis translate_off
 reg[31:0] metadata[0:63];
 // synthesis translate_on
 wire[31:0] mq_a,mq_b;reg[31:0] history_hold,best_frequency;
 reg[5:0] ma,mb;reg mw_a,mw_b,mr_a,mr_b;reg[31:0] md_a,md_b;
 reg[1:0] hc[0:MAX_CENTERS-1];reg[MAX_CENTERS-1:0] alive,fresh,miss,seen,blocked;
 reg[OBS_CENTERS-1:0] used;
 reg[2:0] map[0:OBS_CENTERS-1];reg[OBS_CENTERS-1:0] observed_reach[0:OBS_CENTERS-1];
 reg[MAX_CENTERS-1:0] reach[0:MAX_CENTERS-1],gm[0:2];
 reg[4:0] state;reg[2:0] ti,pi,bi,bp,k,index,group_index;
 reg[31:0] best_distance,next_id,batch_seq;reg bad_batch,has_completed,batch_capacity_overflow;
 reg[MAX_CENTERS-1:0] selected,best_group;reg[34:0] best_power;reg[31:0] best_id;
 integer i,j;reg[34:0] score;reg[31:0] group_min_id;
 reg[2:0] score_member;reg[1:0] distance_mode;reg[31:0] distance,center_hold;
 reg[4:0] service_window;
 reg[31:0] batch_window_end,af[0:4],chosen_frequency,aux_distance,aux_best_distance;
 reg[2:0] ac,aux_scan,aux_best_index;reg[4:0] aux_used;
 reg batch_aux_valid,aux_match_found;reg[31:0] group_min_frequency;
 wire[31:0] aux_age=window_end_sample-aux_window_end;
 wire aux_snapshot_valid=aux_valid && aux_count<=5 && !aux_age[31] && aux_age<=192;
 // AUTO detector frequencies have nineteen exact zero low bits. CF/H0
 // share a RAM with arbitrary powers, which hides this fact from inference.
 // Re-express that invariant only on frequency reads; power data is unchanged.
 // Default=0 preserves the general controller's arbitrary-FCW interface.
 function[31:0] frequency_word;
   input[31:0] value;
   begin frequency_word=GRID_ALIGNED ? {value[31:19],19'd0}:value;end
 endfunction
 wire[31:0] frequency_a=frequency_word(mq_a),frequency_b=frequency_word(mq_b);
 // synthesis translate_off
 integer grid_check;
 always @(posedge clk)if(rst_n && GRID_ALIGNED)begin
   if(cand_valid && cand_fcw[18:0]!==19'd0)$fatal(1,"AUTO main FCW violated grid invariant");
   if(obs_begin && obs_ready && aux_snapshot_valid)
     for(grid_check=0;grid_check<5;grid_check=grid_check+1)
       if(grid_check<aux_count && aux_frequencies[grid_check*32+:19]!==19'd0)
         $fatal(1,"AUTO auxiliary FCW violated grid invariant");
 end
 // synthesis translate_on
 wire[31:0] operand_a=distance_mode==1 ? frequency_a:tf[ti];
 wire[31:0] operand_b=distance_mode==2 ? tf[pi]:(distance_mode==1 ? frequency_b:frequency_a);
 wire[32:0] distance_difference={1'b0,operand_a}-{1'b0,operand_b};
 wire[31:0] distance_comb=distance_difference[32] ? -distance_difference[31:0]:distance_difference[31:0];
 reg[2:0] free_slot;integer fs;
 always @*begin free_slot=7;for(fs=MAX_CENTERS-1;fs>=0;fs=fs-1)if(!alive[fs])free_slot=fs;end
 function[31:0] distance_of;
   input[31:0] a,b;reg[32:0] difference;begin
     difference={1'b0,a}-{1'b0,b};
     distance_of=difference[32] ? -difference[31:0]:difference[31:0];end
 endfunction
 function[2:0] pop;
   input[OBS_CENTERS-1:0] v;integer pv;begin pop=0;for(pv=0;pv<OBS_CENTERS;pv=pv+1)pop=pop+v[pv];end
 endfunction
 function[31:0] median3;
   input[31:0] a,b,c;begin
     median3=a>b ? (b>c ? b:(a>c ? c:a)):(a>c ? a:(b>c ? c:b));
   end
 endfunction
 function[31:0] round_grid;
   input[32:0] twice;reg[13:0] q;begin
     q={1'b0,twice[32:20]};
     if(twice[19:0]>20'h80000 || (twice[19:0]==20'h80000 && q[0]))q=q+1'b1;
     round_grid={q[12:0],19'd0};
   end
 endfunction
 wire[32:0] twice_median=hc[bi]==1 ? {1'b0,frequency_a}+{1'b0,chosen_frequency}:
                                  {median3(frequency_a,h1[bi],chosen_frequency),1'b0};
 wire[31:0] rounded_center=round_grid(twice_median);
 wire center_change=distance_of(tf[bi],center_hold)>GRID;
 wire[31:0] next_track_version=tv[bi]+1'b1;
 always @*begin
   ma=0;mb=0;mw_a=0;mw_b=0;mr_a=0;mr_b=0;md_a=0;md_b=0;
   case(state)
     0:if(cand_valid && locked && count<OBS_CENTERS)begin
       ma={3'd0,count};mb=6'd8+{3'd0,count};mw_a=1;mw_b=1;
       md_a=frequency_word(cand_fcw);md_b=cand_power;
     end
     1,27:begin ma=0;mr_a=1;end
     2:begin ma=pi==OBS_CENTERS-1 ? 6'd0:{3'd0,pi}+6'd1;mr_a=1;end
     3:begin ma=6'd16+{3'd0,bi};mr_a=1;end
     26:begin ma=6'd8+{3'd0,bp};mr_a=1;end
     16:begin
       ma=6'd32+{3'd0,bi};mb=6'd16+{3'd0,bi};mw_a=1;mw_b=1;
       md_a=mq_a;md_b=chosen_frequency;if(capacity_block_new)mw_b=0;
     end
     4:if(index==MAX_CENTERS-1)begin ma=0;mb=8;mr_a=1;mr_b=1;end
     5:begin
       if(index<count && cs[index] && !diagnostic[index] && !used[index] && !capacity_block_new && free_slot<MAX_CENTERS)begin
         ma=6'd32+{3'd0,free_slot};mb=6'd16+{3'd0,free_slot};mw_a=1;mw_b=1;
         md_a=mq_b;md_b=frequency_a;
       end else if(index<OBS_CENTERS-1)begin
         ma={3'd0,index}+6'd1;mb=6'd9+{3'd0,index};mr_a=1;mr_b=1;
       end
     end
     28:begin ma={3'd0,index};mb=6'd8+{3'd0,index};mr_a=1;mr_b=1;end
     6:begin ma=0;mb=0;mr_a=1;mr_b=1;end
     20:if(distance_mode==1)begin
       ma={3'd0,ti}+(pi==OBS_CENTERS-1 ? 6'd1:6'd0);mb=pi==OBS_CENTERS-1 ? 6'd0:{3'd0,pi}+6'd1;
       mr_a=1;mr_b=1;
     end
     12:begin ma=32;mr_a=1;end
     21:if(score_member<MAX_CENTERS-1)begin ma=6'd33+{3'd0,score_member};mr_a=1;end
   endcase
 end
 // synthesis translate_off
 always @(posedge clk)begin
   if(mw_a && rst_n)metadata[ma]<=md_a;
   if(mw_b && rst_n)metadata[mb]<=md_b;
 end
 // synthesis translate_on
 wire metadata_write_a=mw_a && rst_n,metadata_write_b=mw_b && rst_n;
 wire metadata_read_a=mr_a && !metadata_write_a,metadata_read_b=mr_b && !metadata_write_b;
 controller_metadata_dpb32 metadata_bank(.clk(clk),.addr_a(ma),.addr_b(mb),
   .write_a(metadata_write_a),.write_b(metadata_write_b),
   .read_a(metadata_read_a),.read_b(metadata_read_b),
   .data_a(md_a),.data_b(md_b),.q_a(mq_a),.q_b(mq_b));
 // Both-write states use distinct field regions: CF/CP, TP/H0. No state
 // enables a read while the other port writes that same address.
 // synthesis translate_off
 always @(posedge clk)if(rst_n)begin
   if(metadata_write_a && metadata_write_b && ma==mb)$fatal(1,"metadata dual write collision");
   if(metadata_write_a && metadata_read_b && ma==mb)$fatal(1,"metadata Awrite/Bread collision");
   if(metadata_write_b && metadata_read_a && ma==mb)$fatal(1,"metadata Bwrite/Aread collision");
 end
 // synthesis translate_on
 // Reserve 16 unlocked clocks between batches so registered configuration
 // requests can make progress even when a producer always has observations.
 assign obs_ready=(state==0 && !locked && service_window==16 && !obs_fault);
 assign alive_mask=alive;assign strong_mask=fresh;
 assign groups={gm[2],gm[1],gm[0]};
 assign display_ambiguous=|blocked;
 genvar g;generate for(g=0;g<MAX_CENTERS;g=g+1)begin:export_tracks
   assign frequencies[g*32+:32]=tf[g];assign identities[g*32+:32]=tid[g];assign versions[g*32+:32]=tv[g];
 end endgenerate
 always @(posedge clk)begin
   if(!rst_n || locked || obs_begin || obs_fault)service_window<=0;
   else if(service_window<16)service_window<=service_window+1'b1;
 end
 always @(posedge clk)begin
   obs_done<=0;
   if(!rst_n)begin
     state<=0;locked<=0;obs_done<=0;completed_obs<=0;has_completed<=0;
     cs<=0;count<=0;alive<=0;fresh<=0;miss<=0;seen<=0;used<=0;blocked<=0;
     next_id<=0;batch_seq<=0;bad_batch<=0;ti<=0;pi<=0;bi<=0;bp<=0;k<=0;index<=0;group_index<=0;
     best_distance<=MATCH+1;selected<=0;best_group<=0;best_power<=0;best_id<=32'hffffffff;
     invalid_batches<=0;ambiguous_batches<=0;capacity_overflow_batches<=0;batch_capacity_overflow<=0;capacity_block_new<=0;diagnostic<=0;
     score_member<=0;distance_mode<=0;distance<=0;center_hold<=0;score<=0;group_min_id<=32'hffffffff;
     batch_window_end<=0;completed_window_end<=0;fault_events<=0;aux_rejected<=0;
     ac<=0;aux_scan<=0;aux_best_index<=0;aux_used<=0;batch_aux_valid<=0;aux_match_found<=0;
     chosen_frequency<=0;aux_distance<=0;aux_best_distance<=LONG_MATCH+1;group_min_frequency<=32'hffffffff;
     history_hold<=0;best_frequency<=0;
     for(i=0;i<MAX_CENTERS;i=i+1)begin tf[i]<=0;tid[i]<=0;tv[i]<=0;h1[i]<=0;hc[i]<=0;reach[i]<=0;end
     for(i=0;i<OBS_CENTERS;i=i+1)begin map[i]<=7;observed_reach[i]<=0;end
     for(i=0;i<5;i=i+1)af[i]<=0;
     for(i=0;i<3;i=i+1)gm[i]<=0;
   end else if(obs_fault)begin
     // Failed/late analysis is a separate event, never an empty observation.
     // Abort any partial batch and invalidate permissions immediately; keep
     // next_id monotonic so old queued/prepared contributions cannot revive.
     state<=0;locked<=0;count<=0;cs<=0;bad_batch<=0;
     alive<=0;fresh<=0;miss<=0;seen<=0;used<=0;blocked<=0;
     batch_aux_valid<=0;aux_used<=0;fault_events<=fault_events+1'b1;
     for(i=0;i<3;i=i+1)gm[i]<=0;
   end else case(state)
     0:begin
       if(obs_begin && obs_ready)begin
         locked<=1;count<=0;cs<=0;diagnostic<=0;batch_capacity_overflow<=obs_block_new;capacity_block_new<=obs_block_new;batch_seq<=obs_seq;bad_batch<=has_completed && obs_seq<=completed_obs;
         batch_window_end<=window_end_sample;batch_aux_valid<=aux_snapshot_valid;
         ac<=aux_count;aux_used<=0;
         for(i=0;i<5;i=i+1)af[i]<=frequency_word(aux_frequencies[i*32+:32]);
         if(aux_valid && !aux_snapshot_valid)aux_rejected<=aux_rejected+1'b1;
       end
       if(cand_valid && locked)begin
         if(count<OBS_CENTERS)begin
           cs[count]<=cand_strong;diagnostic[count]<=cand_diagnostic;count<=count+1'b1;
         end
         else bad_batch<=1;
       end
       if(obs_end && locked)begin
         if(bad_batch || cand_valid)begin
           // A malformed/replayed batch supplies no usable current evidence.
           // Fail closed; unlike a valid missing observation, this is a
           // protocol fault. Preserve next_id so stale jobs cannot revive.
           invalid_batches<=invalid_batches+1'b1;locked<=0;
           alive<=0;fresh<=0;miss<=0;
           for(i=0;i<3;i=i+1)gm[i]<=0;
         end
         else state<=1;
       end
     end
     1:begin
       seen<=0;used<=0;blocked<=0;fresh<=0;ti<=0;pi<=0;best_distance<=MATCH+1;
       for(i=0;i<OBS_CENTERS;i=i+1)map[i]<=7;
       distance_mode<=0;state<=15;
     end
     15:begin distance<=distance_comb;state<=2;end
     2:begin
       if(alive[ti] && !seen[ti] && pi<count && cs[pi] && !diagnostic[pi] && !used[pi] &&
          (distance<best_distance || (distance==best_distance &&
           (tid[ti]<tid[bi] || (tid[ti]==tid[bi] && pi<bp)))))begin
         best_distance<=distance;bi<=ti;bp<=pi;best_frequency<=frequency_a;
       end
       state<=15;
       if(pi==OBS_CENTERS-1)begin pi<=0;if(ti==MAX_CENTERS-1)state<=3;else ti<=ti+1'b1;end else pi<=pi+1'b1;
     end
     3:if(best_distance<=MATCH)begin
       // Main/raw frequency remains authoritative for matching and ambiguity.
       // Auxiliary data only changes the history observation of this existing
       // strong match, and cannot be used during one-miss reacquisition.
       chosen_frequency<=best_frequency;aux_scan<=0;aux_best_distance<=LONG_MATCH+1;
       aux_match_found<=0;aux_best_index<=0;
       state<=batch_aux_valid && !miss[bi] ? 5'd23:5'd26;
     end else begin index<=0;state<=4;end
     23:begin aux_distance<=distance_of(best_frequency,af[aux_scan]);state<=24;end
     24:begin
       if(aux_scan<ac && !aux_used[aux_scan] && af[aux_scan]!=0 && af[aux_scan]<32'h80000000 &&
          aux_distance<=LONG_MATCH && aux_distance<aux_best_distance)begin
         aux_best_distance<=aux_distance;aux_best_index<=aux_scan;aux_match_found<=1;
       end
       if(aux_scan==4)state<=25;else begin aux_scan<=aux_scan+1'b1;state<=23;end
     end
     25:begin
       if(aux_match_found)begin chosen_frequency<=af[aux_best_index];aux_used[aux_best_index]<=1;end
       state<=26;
     end
     26:begin center_hold<=rounded_center;history_hold<=frequency_a;state<=16;end
     16:begin
       seen[bi]<=1;fresh[bi]<=1;used[bp]<=1;miss[bi]<=0;
       for(i=0;i<OBS_CENTERS;i=i+1)if(bp==i)map[i]<=bi;
       for(i=0;i<MAX_CENTERS;i=i+1)begin
         if(bi==i && !capacity_block_new)begin
           if(miss[i])begin tv[i]<=next_track_version;tf[i]<=chosen_frequency;hc[i]<=1;h1[i]<=0;end
           else begin
             h1[i]<=history_hold;hc[i]<=2;
             if(center_change)begin tf[i]<=center_hold;tv[i]<=next_track_version;end
           end
         end
       end
       ti<=0;pi<=0;best_distance<=MATCH+1;state<=27;
     end
     27:state<=15;
     4:begin
       if(alive[index] && !seen[index])begin
         miss[index]<=1;if(miss[index])alive[index]<=0;
       end
       if(index==MAX_CENTERS-1)begin index<=0;state<=5;end else index<=index+1'b1;
     end
     5:begin
       if(index<count && cs[index] && !diagnostic[index] && !used[index] && !capacity_block_new && free_slot<MAX_CENTERS)begin
         alive[free_slot]<=1;fresh[free_slot]<=1;miss[free_slot]<=0;
         for(i=0;i<MAX_CENTERS;i=i+1)begin
           if(free_slot==i)begin
             tf[i]<=frequency_a;tid[i]<=next_id;tv[i]<=1;
             h1[i]<=0;hc[i]<=1;
           end
         end
         for(i=0;i<OBS_CENTERS;i=i+1)if(index==i)map[i]<=free_slot;
         next_id<=next_id+1'b1;
       end
       if(index<count && cs[index] && !used[index] && free_slot>=MAX_CENTERS)batch_capacity_overflow<=1;
       if(index==OBS_CENTERS-1)state<=6;
       else begin
         index<=index+1'b1;
         if(index<count && cs[index] && !diagnostic[index] && !used[index] && !capacity_block_new && free_slot<MAX_CENTERS)state<=28;
       end
     end
     28:state<=5;
     6:begin
       // All raw fresh candidates participate, including capacity-deferred ones.
       ti<=0;pi<=0;distance_mode<=1;state<=19;
     end
     19:begin distance<=distance_comb;state<=20;end
     20:begin
       if(distance_mode==1)begin
         for(i=0;i<OBS_CENTERS;i=i+1)if(ti==i)
           observed_reach[i][pi]<=ti<count && pi<count && cs[ti] && cs[pi] && distance<=GROUP;
       end else begin
         for(i=0;i<MAX_CENTERS;i=i+1)if(ti==i)reach[i][pi]<=fresh[ti] && fresh[pi] && distance<=GROUP;
       end
       state<=19;
       if(pi==(distance_mode==1 ? OBS_CENTERS-1:MAX_CENTERS-1))begin
         pi<=0;
         if(ti==(distance_mode==1 ? OBS_CENTERS-1:MAX_CENTERS-1))begin k<=0;state<=distance_mode==1?5'd7:5'd10;end
         else ti<=ti+1'b1;
       end else pi<=pi+1'b1;
     end
     7:begin
       for(i=0;i<OBS_CENTERS;i=i+1)if(observed_reach[i][k])observed_reach[i]<=observed_reach[i]|observed_reach[k];
       if(k==OBS_CENTERS-1)begin index<=0;state<=8;end else k<=k+1'b1;
     end
     8:begin
       if(pop(observed_reach[index])>2 && map[index]<MAX_CENTERS)blocked[map[index]]<=1;
       if(index==OBS_CENTERS-1)state<=9;else index<=index+1'b1;
     end
     9:begin
       ti<=0;pi<=0;distance_mode<=2;state<=19;
     end
     10:begin
       for(i=0;i<MAX_CENTERS;i=i+1)if(reach[i][k])reach[i]<=reach[i]|reach[k];
       if(k==MAX_CENTERS-1)begin index<=0;state<=11;end else k<=k+1'b1;
     end
     11:begin
       for(i=0;i<MAX_CENTERS;i=i+1)if(pop(reach[i])>2)blocked[i]<=1;
       selected<=0;group_index<=0;index<=0;best_group<=0;best_power<=0;best_id<=32'hffffffff;
       for(i=0;i<3;i=i+1)gm[i]<=0;
       state<=12;
     end
     12:begin
       score<=0;group_min_id<=32'hffffffff;group_min_frequency<=32'hffffffff;score_member<=0;state<=21;
     end
     21:begin
       if(reach[index][score_member])begin
         score<=score+{3'd0,mq_a};
         if(tf[score_member]<group_min_frequency ||
            (tf[score_member]==group_min_frequency && tid[score_member]<group_min_id))begin
           group_min_frequency<=tf[score_member];group_min_id<=tid[score_member];
         end
       end
       if(score_member==MAX_CENTERS-1)state<=22;else score_member<=score_member+1'b1;
     end
     22:begin
       if(fresh[index] && !(reach[index]&blocked) && !(reach[index]&selected) &&
          (score>best_power || (score==best_power && group_min_id<best_id)))begin
         best_power<=score;best_id<=group_min_id;best_group<=reach[index];
       end
       if(index==MAX_CENTERS-1)state<=13;else begin index<=index+1'b1;state<=12;end
     end
     13:begin
       for(i=0;i<3;i=i+1)if(group_index==i)gm[i]<=best_group;
       selected<=selected|best_group;
       if(group_index==2 || best_group==0)state<=14;
       else begin group_index<=group_index+1'b1;index<=0;best_group<=0;best_power<=0;best_id<=32'hffffffff;state<=12;end
     end
     14:begin
       completed_obs<=batch_seq;completed_window_end<=batch_window_end;has_completed<=1;obs_done<=1;locked<=0;state<=0;
       if(blocked!=0)ambiguous_batches<=ambiguous_batches+1'b1;
       if(batch_capacity_overflow)capacity_overflow_batches<=capacity_overflow_batches+1'b1;
     end
     default:state<=0;
   endcase
 end
endmodule

// Supported AroraV25K mode00; two16-bit lanes, same6-bit logical address.
// READ_MODE0 bypasses only the optional extra pipeline register: synchronous
// input clock still produces the read at that edge. Normal-mode writes hold
// the output exactly as original if(write) ... else if(read) RTL intended.
module controller_metadata_dpb32(input clk,input[5:0] addr_a,input[5:0] addr_b,
 input write_a,input write_b,input read_a,input read_b,
 input[31:0] data_a,input[31:0] data_b,output[31:0] q_a,output[31:0] q_b);
 wire[13:0] physical_a={4'b0000,addr_a,2'b00,2'b11};
 wire[13:0] physical_b={4'b0000,addr_b,2'b00,2'b11};
 genvar lane;
 generate for(lane=0;lane<2;lane=lane+1)begin:metadata_lane
   DPB #(.READ_MODE0(1'b0),.READ_MODE1(1'b0),
     .WRITE_MODE0(2'b00),.WRITE_MODE1(2'b00),
     .BIT_WIDTH_0(16),.BIT_WIDTH_1(16),.BLK_SEL_0(3'b000),.BLK_SEL_1(3'b000),.RESET_MODE("SYNC")) ram(
     .DOA(q_a[16*lane+:16]),.DOB(q_b[16*lane+:16]),
     .DIA(data_a[16*lane+:16]),.DIB(data_b[16*lane+:16]),
     .BLKSELA(3'b000),.BLKSELB(3'b000),.ADA(physical_a),.ADB(physical_b),
     .WREA(write_a),.WREB(write_b),.CLKA(clk),.CLKB(clk),
     .CEA(write_a || read_a),.CEB(write_b || read_b),.OCEA(1'b1),.OCEB(1'b1),
     .RESETA(1'b0),.RESETB(1'b0));
 end endgenerate
endmodule
