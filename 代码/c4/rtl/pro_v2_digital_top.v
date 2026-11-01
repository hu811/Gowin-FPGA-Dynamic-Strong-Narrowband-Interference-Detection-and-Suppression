`timescale 1ns/1ps
// V18 automatic detector/executor, driven only by CRC-validated digital samples.
// PC supplies mixed samples and records results; no source labels enter the core.
module pro_v2_digital_top #(parameter MAX_CENTERS=4)
 (input clk,input reset_n,input uart_rx_pin,output uart_tx_pin,
  output[3:0] led,inout oled_scl,inout oled_sda);
 wire board_reset,core_reset;
 wire rst_n=board_reset && !core_reset;
 reset_sync resetter(clk,reset_n,board_reset);
 wire iv,ov,ready;wire signed[15:0] x,y;wire[31:0] seq,os;
 wire[31:0] dropped,saturated,commits,generation,errors;
 wire[15:0] max_fir;wire[1:0] waiting;wire running;
 wire[MAX_CENTERS-1:0] active,astr;wire[3*MAX_CENTERS-1:0] groups;
 wire[31:0] deadlines,seqfaults,numeric,frames;
 wire[7:0] error_code;wire[32*MAX_CENTERS-1:0] af;
 // 0x80 is reserved here for executor aggregate faults without a core code.
 // Preserve specific detector codes (including 0x81/0x82/0x9x/0xf0/0xff).
 wire[7:0] link_error_code=error_code!=0 ? error_code:(errors!=0 ? 8'h80:8'd0);
 wire[16:0] display_cycles;wire display_time_overflow,display_fading,display_ambiguous;
 wire[3*MAX_CENTERS-1:0] display_groups;
 wire capacity_block_new;
 wire[127:0] af_export={{(128-32*MAX_CENTERS){1'b0}},af};
 wire[7:0] active_export={{(8-MAX_CENTERS){1'b0}},active};
 wire[14:0] groups_export={
   {{(5-MAX_CENTERS){1'b0}},groups[2*MAX_CENTERS+:MAX_CENTERS]},
   {{(5-MAX_CENTERS){1'b0}},groups[MAX_CENTERS+:MAX_CENTERS]},
   {{(5-MAX_CENTERS){1'b0}},groups[0+:MAX_CENTERS]}};
 wire[15:0] link_faults;wire started,streaming,complete;

 digital_link #(.CAPACITY(MAX_CENTERS)) link(.clk(clk),.rst_n(board_reset),
   .rx(uart_rx_pin),.tx(uart_tx_pin),.core_reset(core_reset),
   .in_valid(iv),.in_data(x),.in_seq(seq),.core_ready(ready),
   .out_valid(ov),.out_data(y),.out_seq(os),
   .commits(commits),.frames(frames),.dropped(dropped),.numeric(numeric),
   .deadline(deadlines),.sequence_faults(seqfaults),.saturated(saturated),
   .active(active_export),.error_code(link_error_code),.groups(groups_export),
   .max_fir(max_fir),.authorized_frequencies(af_export),
   .waiting(waiting),.task_running(running),.link_faults(link_faults),
   .started(started),.streaming(streaming),.complete(complete));

 pro_v2_auto_core #(.MAX_CENTERS(MAX_CENTERS)) automatic_core(
   .clk(clk),.rst_n(rst_n),.in_valid(iv),.in_data(x),.in_seq(seq),.ready(ready),
   .out_valid(ov),.out_data(y),.out_seq(os),.dropped(dropped),.saturated(saturated),
   .max_fir_cycles(max_fir),.commits(commits),.generation_cycles(generation),
   .waiting(waiting),.running(running),.active_mask(active),.active_groups(groups),
   .executor_errors(errors),.deadline_faults(deadlines),.sequence_faults(seqfaults),
   .numeric_faults(numeric),.last_error(error_code),.completed_frames(frames),
   .authorized_frequencies(af),.authorized_strong(astr),
   .capacity_block_new(capacity_block_new),.display_cycles(display_cycles),
   .display_time_overflow(display_time_overflow),.display_groups(display_groups),
   .display_fading(display_fading),.display_ambiguous(display_ambiguous));

 oled_dashboard #(.MAX_CENTERS(MAX_CENTERS)) oled(.clk(clk),.rst_n(board_reset),
   .groups(display_groups),.active(active),.waiting(waiting),.running(running),
   .fading(display_fading),.observed(frames!=0),.time_cycles(display_cycles),
   .time_overflow(display_time_overflow),
   .fault(errors!=0 || dropped!=0 || saturated!=0 || deadlines!=0 ||
          seqfaults!=0 || numeric!=0 || link_faults!=0),
   .blocked(capacity_block_new),.ambiguous(display_ambiguous),
   .oled_scl(oled_scl),.oled_sda(oled_sda),.link_ok());
 reg fault_led;
 always @(posedge clk)begin
   if(!board_reset)fault_led<=0;
   else fault_led<=(errors!=0 || dropped!=0 || saturated!=0 || link_faults!=0);
 end
 assign led=~{fault_led,streaming,started,seq[14]};
endmodule

module pro_v2_digital_top_3(input clk,input reset_n,input uart_rx_pin,
 output uart_tx_pin,output[3:0] led,inout oled_scl,inout oled_sda);
 pro_v2_digital_top #(.MAX_CENTERS(3)) complete_digital(
  .clk(clk),.reset_n(reset_n),.uart_rx_pin(uart_rx_pin),.uart_tx_pin(uart_tx_pin),
  .led(led),.oled_scl(oled_scl),.oled_sda(oled_sda));
endmodule

module pro_v2_digital_top_4(input clk,input reset_n,input uart_rx_pin,
 output uart_tx_pin,output[3:0] led,inout oled_scl,inout oled_sda);
 pro_v2_digital_top #(.MAX_CENTERS(4)) complete_digital(
  .clk(clk),.reset_n(reset_n),.uart_rx_pin(uart_rx_pin),.uart_tx_pin(uart_tx_pin),
  .led(led),.oled_scl(oled_scl),.oled_sda(oled_sda));
endmodule
