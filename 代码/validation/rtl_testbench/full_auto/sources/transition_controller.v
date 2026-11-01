`timescale 1ns/1ps
// A ready bank can start only at accepted input; alpha endpoints exactly 1..32.
// The old bank is released only AFTER the alpha=32 output finishes reading it.
module transition_controller(input clk,input rst_n,input sample_accept,input sample_done,
 input proposal_valid,input[1:0] proposal_bank,output proposal_accept,
 output[1:0] old_bank,output old_enable,output[1:0] new_bank,output new_enable,
 output fading,output[5:0] alpha,output[2:0] occupied,
 output reg[31:0] commits,output reg[31:0] active_version,input[31:0] proposal_version);
 reg[1:0] active,target;reg active_on,transition,last_pending;reg[5:0] step;
 reg[31:0] target_version;
 wire launch=proposal_valid && !transition;
 assign proposal_accept=sample_accept && launch;
 assign old_bank=active;assign old_enable=active_on;
 assign new_bank=transition ? target:proposal_bank;
 assign new_enable=1;
 assign fading=transition || launch;
 assign alpha=transition ? step:6'd1;
 assign occupied=(3'b001<<active) | (transition ? (3'b001<<target):3'b000);
 always @(posedge clk)begin
   if(!rst_n)begin
     active<=0;target<=1;active_on<=0;transition<=0;last_pending<=0;step<=1;
     commits<=0;active_version<=0;target_version<=0;
   end else begin
     if(proposal_accept)begin
       target<=proposal_bank;target_version<=proposal_version;transition<=1;step<=2;
       commits<=commits+1'b1;
     end else if(sample_accept && transition)begin
       if(step==32)last_pending<=1;else step<=step+1'b1;
     end
     if(sample_done && last_pending)begin
       active<=target;active_on<=1;active_version<=target_version;
       transition<=0;last_pending<=0;step<=1;
     end
   end
 end
endmodule
