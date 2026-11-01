`timescale 1ns/1ps
// Separate analysis history. Snapshot the causal 1024-sample window in 1026
// clocks (<1562 sample cadence); load its tail into the shared 512 FFT while
// retaining the full snapshot for this same observation's optional 1024 FFT.
module analysis_frontend #(parameter ALLOW_FFT_OVERLAP=0)(input clk,input rst_n,input in_valid,input signed[15:0] in_data,
 input[31:0] in_seq,input detector_busy,
 output main_valid,output[8:0] main_bin,output signed[31:0] main_real,main_imag,
 output[63:0] main_power,output main_done,
 output aux_valid,output[9:0] aux_bin,output[63:0] aux_power,output aux_done,
 output reg[31:0] frame_seq,output reg[31:0] window_end_sample,
 output reg[31:0] window_end_clock,output reg[31:0] main_end_clock,
 output reg[31:0] fault_window_end,output reg[31:0] fault_clock,
 output reg abort,output busy,output reg[31:0] deadline_faults,
 output reg[31:0] sequence_faults,output reg[31:0] max_front_cycles);
 reg signed[15:0] history[0:1023] /* synthesis syn_ramstyle="block_ram" */;
 reg signed[15:0] auxiliary_window[0:1023] /* synthesis syn_ramstyle="block_ram" */;
 reg signed[15:0] history_q,auxiliary_q;
 reg[9:0] write_pointer,history_address,auxiliary_address,copy_index;
 reg[10:0] filled,snapshot_filled;
 reg[31:0] last_sequence,clock_count,front_cycles;reg have_sequence,large_due;
 reg[3:0] state;
 wire[15:0] snapshot_data=({1'b0,copy_index} < (11'd1024-snapshot_filled))?16'd0:history_q;
 wire fft_load=(state==2 && copy_index>=512) || state==6;
 wire[9:0] fft_load_address=state==6?copy_index:{1'b0,copy_index[8:0]};
 wire signed[15:0] fft_load_sample=state==6?auxiliary_q:snapshot_data;
 wire fft_start=state==3 || state==7;
 wire fft_large=state==7;
 wire fft_busy,fft_done,fft_valid;wire[9:0] fft_bin;
 wire signed[31:0] fft_real,fft_imag;wire[63:0] fft_power;
 wire[31:0] fft_frame,fft_cycles,rejected;
 analysis_fft fft(.clk(clk),.rst_n(rst_n && !abort),.load_we(fft_load),
   .load_addr(fft_load_address),.load_sample(fft_load_sample),.start(fft_start),
   .mode_1024(fft_large),.frame_seq(frame_seq),.busy(fft_busy),.done(fft_done),
   .out_valid(fft_valid),.out_bin(fft_bin),.out_real(fft_real),.out_imag(fft_imag),
   .out_power(fft_power),.out_frame_seq(fft_frame),.frame_cycles(fft_cycles),.rejected_starts(rejected));
 // In overlap mode the next FFT can compute while the previous observation
 // finishes. Its first output is a hard ownership boundary: no new raw FFT
 // value may overwrite the fitter/gate memories while their old frame is live.
 wire output_conflict=ALLOW_FFT_OVERLAP && state==4 && fft_valid && fft_bin==0 && detector_busy;
 assign main_valid=fft_valid && state==4 && !abort && !output_conflict;
 assign main_bin=fft_bin[8:0];assign main_real=fft_real;assign main_imag=fft_imag;assign main_power=fft_power;
 assign main_done=fft_done && state==4 && !abort;
 assign aux_valid=fft_valid && state==8 && !abort;assign aux_bin=fft_bin;assign aux_power=fft_power;
 assign aux_done=fft_done && state==8 && !abort;assign busy=state!=0;
 always @(posedge clk)begin
   if(in_valid)history[write_pointer]<=in_data;
   history_q<=history[history_address];
   auxiliary_q<=auxiliary_window[auxiliary_address];
   if(state==2)auxiliary_window[copy_index]<=snapshot_data;
 end
 always @(posedge clk)begin
   abort<=0;
   if(!rst_n)begin
     write_pointer<=0;history_address<=0;auxiliary_address<=0;copy_index<=0;filled<=0;
     snapshot_filled<=0;last_sequence<=0;clock_count<=0;front_cycles<=0;have_sequence<=0;
     state<=0;large_due<=0;frame_seq<=0;window_end_sample<=0;window_end_clock<=0;
     main_end_clock<=0;fault_window_end<=0;fault_clock<=0;
     abort<=0;deadline_faults<=0;sequence_faults<=0;max_front_cycles<=0;
   end else begin
     clock_count<=clock_count+1'b1;
     if(state!=0)front_cycles<=front_cycles+1'b1;
     if(in_valid)begin
       write_pointer<=write_pointer+1'b1;last_sequence<=in_seq;have_sequence<=1;
       if(filled<1024)filled<=filled+1'b1;
     end
     case(state)
       0:if(in_valid && in_seq[5:0]==63 && filled>=511)begin
         if((detector_busy && !ALLOW_FFT_OVERLAP) || fft_busy)begin
           abort<=1;deadline_faults<=deadline_faults+1'b1;
           fault_window_end<=in_seq+1'b1;fault_clock<=clock_count;
         end
         else begin
           frame_seq<=(in_seq-32'd255)>>6;window_end_sample<=in_seq+1'b1;
           window_end_clock<=clock_count;front_cycles<=1;
           large_due<=in_seq[7:0]==255 && filled>=1023;
           snapshot_filled<=filled<1024?filled+1'b1:11'd1024;
           history_address<=write_pointer+1'b1;copy_index<=0;state<=1;
         end
       end
       1:begin history_address<=history_address+1'b1;state<=2;end
       2:begin
         history_address<=history_address+1'b1;
         if(copy_index==1023)state<=3;else copy_index<=copy_index+1'b1;
       end
       3:state<=4;
       4:if(fft_done)begin
         main_end_clock<=clock_count;
         if(large_due)begin auxiliary_address<=0;copy_index<=0;state<=5;end
         else begin if(front_cycles>max_front_cycles)max_front_cycles<=front_cycles;state<=0;end
       end
       5:begin auxiliary_address<=1;state<=6;end
       6:begin
         auxiliary_address<=auxiliary_address+1'b1;
         if(copy_index==1023)state<=7;else copy_index<=copy_index+1'b1;
       end
       7:state<=8;
       8:if(fft_done)begin
         if(front_cycles>max_front_cycles)max_front_cycles<=front_cycles;state<=0;
       end
       default:state<=0;
     endcase
     // A deadline failure is separate from a valid empty observation. It
     // resets analysis only; the FIR history and output path are unaffected.
     if(state!=0 && in_valid && in_seq[5:0]==63 && filled>=511)begin
       abort<=1;state<=0;deadline_faults<=deadline_faults+1'b1;
       fault_window_end<=in_seq+1'b1;fault_clock<=clock_count;
     end
     if(output_conflict)begin
       abort<=1;state<=0;deadline_faults<=deadline_faults+1'b1;
       fault_window_end<=window_end_sample;fault_clock<=clock_count;
     end
     if(in_valid && have_sequence && in_seq!=last_sequence+1'b1)begin
       abort<=1;state<=0;filled<=1;sequence_faults<=sequence_faults+1'b1;
       fault_window_end<=in_seq+1'b1;fault_clock<=clock_count;
     end
   end
 end
endmodule
