`timescale 1ns/1ps
// One nonpreemptive running group and two waiting descriptors. Latest controller
// state is authoritative, not a third waiting FIFO. Invalid queued versions drop;
// full queue defers requests, which the bounded controller scan will retry.
module config_scheduler #(parameter MAX_CENTERS=5)(input clk,input rst_n,input locked,
 input[MAX_CENTERS-1:0] strong_mask,input[32*MAX_CENTERS-1:0] identities,input[32*MAX_CENTERS-1:0] versions,input[3*MAX_CENTERS-1:0] groups,
 input request,input[97*MAX_CENTERS-1:0] descriptor,output request_ready,
 input worker_ready,input worker_done,output reg start,output reg running,
 output[97*MAX_CENTERS-1:0] job,output job_authorized,
 output[1:0] waiting,output reg[31:0] stale_drops,output reg[31:0] replacements,
 output[198+MAX_CENTERS-1:0] compact_job);
 // Valid groups contain at most two members. Store only those member tokens;
 // the public sparse descriptor has 97*MAX_CENTERS bits. Each
 // 99-bit entry is {slot[2:0],ID[31:0],version[31:0],FCW[31:0]}.
 reg[198+MAX_CENTERS-1:0] job_compact,q0,q1;reg v0,v1;
 function[198+MAX_CENTERS-1:0] compact;
   input[97*MAX_CENTERS-1:0] d;integer i;reg[MAX_CENTERS-1:0] first_mask,last_mask,m;
   reg[98:0] first_token,last_token,token;begin
     m=d[96*MAX_CENTERS+:MAX_CENTERS];first_mask=0;last_mask=0;
     // One-hot enables preserve the first/last procedural priorities even
     // for malformed masks. A single-member input has no second token.
     for(i=0;i<MAX_CENTERS;i=i+1)begin
       first_mask[i]=m[i] && ((m & (({{(MAX_CENTERS-1){1'b0}},1'b1}<<i)-1'b1))==0);
       last_mask[i]=m[i] && ((m & (({{(MAX_CENTERS-1){1'b0}},1'b1}<<i)-1'b1))!=0) && ((m>>(i+1))==0);
     end
     first_token=0;last_token=0;
     for(i=0;i<MAX_CENTERS;i=i+1)begin
       token={i[2:0],d[64*MAX_CENTERS+i*32+:32],d[32*MAX_CENTERS+i*32+:32],d[i*32+:32]};
       first_token=first_token | (token & {99{first_mask[i]}});
       last_token=last_token | (token & {99{last_mask[i]}});
     end
     compact={m,last_token,first_token};
   end
 endfunction
 function[97*MAX_CENTERS-1:0] expand;
   input[198+MAX_CENTERS-1:0] c;integer s;reg[2:0] count;reg[MAX_CENTERS-1:0] m;reg[97*MAX_CENTERS-1:0] d;begin
     d=0;d[96*MAX_CENTERS+:MAX_CENTERS]=c[198+:MAX_CENTERS];
     m=c[198+:MAX_CENTERS];count=0;for(s=0;s<MAX_CENTERS;s=s+1)count=count+m[s];
     // Constant destination slices avoid a 485-bit variable-write shifter.
     // The second token retains the old procedural priority if slots collide.
     for(s=0;s<MAX_CENTERS;s=s+1)begin
       if(count>0 && c[98:96]==s && m[s])begin
         d[s*32+:32]=c[31:0];d[32*MAX_CENTERS+s*32+:32]=c[63:32];d[64*MAX_CENTERS+s*32+:32]=c[95:64];
       end
       if(count>1 && c[197:195]==s && m[s])begin
         d[s*32+:32]=c[130:99];d[32*MAX_CENTERS+s*32+:32]=c[162:131];d[64*MAX_CENTERS+s*32+:32]=c[194:163];
       end
     end
     expand=d;
   end
 endfunction
 wire[198+MAX_CENTERS-1:0] request_compact=compact(descriptor);
 assign job=expand(job_compact);
 assign compact_job=job_compact;
 function authorized;
   input[198+MAX_CENTERS-1:0] d;
   input[MAX_CENTERS-1:0] current_strong;input[32*MAX_CENTERS-1:0] current_ids,current_versions;input[3*MAX_CENTERS-1:0] current_groups;
   integer e,ci;reg good;reg[MAX_CENTERS-1:0] m;reg[2:0] slot,count;begin
     m=d[198+:MAX_CENTERS];count=0;for(ci=0;ci<MAX_CENTERS;ci=ci+1)count=count+m[ci];
     good=m!=0 && count<=2 && (m==current_groups[0+:MAX_CENTERS] || m==current_groups[MAX_CENTERS+:MAX_CENTERS] || m==current_groups[2*MAX_CENTERS+:MAX_CENTERS]);
     for(e=0;e<2;e=e+1)if(e<count)begin
       slot=d[e*99+96+:3];
       if(slot>=MAX_CENTERS || !m[slot])good=0;
       else good=good && current_strong[slot] &&
         d[e*99+32+:32]==current_versions[slot*32+:32] && d[e*99+64+:32]==current_ids[slot*32+:32];
     end
     authorized=good;
   end
 endfunction
 // Four-clock decision, with one shared waiting/request authorization unit.
 // Producer holds request/descriptor until handshake; the controller changes
 // IDs/versions/groups under locked, invalidating every partial assessment.
 // The running job retains a separate LIVE authorization check for commit.
 reg decision_valid,request_seen,a0,a1,request_authorized;
 reg[1:0] authorization_phase;
 reg[6:0] equal_job,equal_q0,equal_q1;
 integer piece;
 wire duplicate=(running && (&equal_job)) || (a0 && (&equal_q0)) || (a1 && (&equal_q1));
 // A duplicate handshake is an accepted no-op: a registered producer need not
 // wait for an existing copy to finish before offering the next group.
 // Dispatch has priority over enqueue for a genuinely new descriptor.
 assign request_ready=decision_valid && request_seen && !locked && request_authorized && (duplicate ||
   ((!running && worker_ready) ? (!a0 && !a1) : (!a0 || !a1)));
 // Explicit live inputs make continuous authorization sensitive to evidence
 // changes, not only job updates (functions with hidden global reads may cache
 // the old result in event-driven simulators).
 assign job_authorized=!locked && authorized(job_compact,strong_mask,identities,versions,groups);
 wire[198+MAX_CENTERS-1:0] authorization_job=authorization_phase==0 ? q0:
                              authorization_phase==1 ? q1:request_compact;
 wire authorization_result=authorized(authorization_job,strong_mask,identities,versions,groups);
 assign waiting={1'b0,v0}+{1'b0,v1};
 always @(posedge clk)begin
   start<=0;
   if(!rst_n)begin
     start<=0;running<=0;job_compact<=0;q0<=0;q1<=0;v0<=0;v1<=0;stale_drops<=0;replacements<=0;
     decision_valid<=0;authorization_phase<=0;request_seen<=0;a0<=0;a1<=0;request_authorized<=0;
     equal_job<=0;equal_q0<=0;equal_q1<=0;
   end
   else begin
     if(worker_done)running<=0;
     if(locked)begin decision_valid<=0;authorization_phase<=0;end
     else if(!decision_valid)begin
       case(authorization_phase)
         0:begin a0<=v0 && authorization_result;request_seen<=request;authorization_phase<=1;end
         1:begin a1<=v1 && authorization_result;authorization_phase<=2;end
         2:begin
           request_authorized<=authorization_result;decision_valid<=1;authorization_phase<=0;
         end
       endcase
       for(piece=0;piece<6;piece=piece+1)begin
         equal_job[piece]<=request_compact[piece*32+:32]==job_compact[piece*32+:32];
         equal_q0[piece]<=request_compact[piece*32+:32]==q0[piece*32+:32];
         equal_q1[piece]<=request_compact[piece*32+:32]==q1[piece*32+:32];
       end
       equal_job[6]<=request_compact[198+MAX_CENTERS-1:192]==job_compact[198+MAX_CENTERS-1:192];
       equal_q0[6]<=request_compact[198+MAX_CENTERS-1:192]==q0[198+MAX_CENTERS-1:192];
       equal_q1[6]<=request_compact[198+MAX_CENTERS-1:192]==q1[198+MAX_CENTERS-1:192];
     end else begin
       decision_valid<=0;authorization_phase<=0;
       if(v0&&!a0)v0<=0;if(v1&&!a1)v1<=0;
       if((v0&&!a0)||(v1&&!a1))stale_drops<=stale_drops+{31'd0,(v0&&!a0)}+{31'd0,(v1&&!a1)};
       if(!running && worker_ready)begin
         if(a0)begin job_compact<=q0;start<=1;running<=1;q0<=q1;v0<=a1;v1<=0;end
         else if(a1)begin job_compact<=q1;start<=1;running<=1;v1<=0;end
         else if(request_seen && request && !duplicate && request_authorized)begin job_compact<=request_compact;start<=1;running<=1;end
       end else if(request_ready && request && !duplicate)begin
         if(!a0)begin q0<=request_compact;v0<=1;if(v0)replacements<=replacements+1'b1;end
         else begin q1<=request_compact;v1<=1;if(v1)replacements<=replacements+1'b1;end
       end
     end
   end
 end
endmodule
