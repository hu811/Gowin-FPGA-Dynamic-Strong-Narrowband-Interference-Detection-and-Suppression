`timescale 1ns/1ps
// Finite, fixed-cadence digital validation transport. The host supplies int16
// mixed samples only; all signal analysis and filtering stay in auto_core.
// One true dual-port 1024x16 RAM, input/output rings in separate 512-word halves.
// No memory reset: committed levels and one-word prefetch valid bits own access.
module digital_link #(parameter CAPACITY=4,CLK_HZ=50000000,BAUD=2000000)(
 input clk,input rst_n,input rx,output reg tx,
 output reg core_reset,output reg in_valid,output signed[15:0] in_data,
 output[31:0] in_seq,input core_ready,
 input out_valid,input signed[15:0] out_data,input[31:0] out_seq,
 input[31:0] commits,frames,dropped,numeric,deadline,sequence_faults,saturated,
 input[7:0] active,error_code,input[14:0] groups,
 input[15:0] max_fir,input[127:0] authorized_frequencies,
 input[1:0] waiting,input task_running,
 output reg[15:0] link_faults,output reg started,output reg streaming,output reg complete);
 localparam UART_DIV=(CLK_HZ+BAUD/2)/BAUD;
 localparam STATUS_CYCLES=CLK_HZ/200;
 localparam SAMPLE_WIDTH=$clog2(CLK_HZ);
 localparam STATUS_WIDTH=$clog2(STATUS_CYCLES+1);
 localparam[SAMPLE_WIDTH-1:0] SAMPLE_ADD=32000,SAMPLE_SUB=32000-CLK_HZ;
 localparam[7:0] CAPACITY_BYTE=CAPACITY;
 // Finite validation capture only: <=1,048,575 samples (32.76796875 s).
 localparam[31:0] CAPTURE_MAX=32'h000fffff;

 wire rx_valid,rx_framing;wire[7:0] rx_data;
 digital_link_uart_rx #(.CLK_HZ(CLK_HZ),.BAUD(BAUD)) receiver(
   .clk(clk),.rst_n(rst_n),.rx(rx),.valid(rx_valid),.data(rx_data),.framing(rx_framing));

 reg[19:0] admitted,consumed,total,output_received,output_transmitted;
 wire[19:0] presented_sequence=consumed-20'd1;
 // Outside in_valid the presented value is unspecified; all valid indices
 // remain the original uint32 sequence, zero-extended from the bounded run.
 assign in_seq={12'd0,presented_sequence};
 reg[9:0] input_level,output_level;
 wire core_fault=(dropped!=0 || numeric!=0 || deadline!=0 || sequence_faults!=0 ||
                  saturated!=0 || error_code!=0);
 reg core_failed;
 wire run_fault=(link_faults!=0 || core_fault || core_failed);

 // Fixed-length frame parser. CRC is updated in eight clocks per byte rather
 // than an eight-bit combinational CRC cone. A UART byte takes 250 clocks.
 reg[7:0] rx_position,frame_command,frame_count;
 reg[31:0] frame_argument;
 reg[15:0] frame_faults,rx_crc;
 reg[7:0] rx_crc_low,sample_low;
 reg[3:0] rx_crc_steps;
 reg frame_write_ok;
 reg packet_event,packet_write_ok;
 // The parser does not change these until bytes 4/5/6 of the NEXT frame,
 // at least 1250 UART clocks after this packet_event is consumed.
 wire[7:0] packet_command=frame_command,packet_count=frame_count;
 wire[31:0] packet_argument=frame_argument;
 reg[15:0] packet_faults;
 wire[31:0] argument_complete={rx_data,frame_argument[31:8]};
 wire[10:0] header_level_sum={1'b0,input_level}+{3'd0,frame_count};
 wire[32:0] header_total_sum;
 digital_link_dsp_add33 admitted_sum_unit(.a({12'd0,admitted}),.b({24'd0,frame_count}),.q(header_total_sum));
 wire capture_exceeded=(|header_total_sum[32:20]);
 wire header_low_exceeded;
 digital_link_dsp_less32 total_compare_unit(.a({12'd0,total}),.b(header_total_sum[31:0]),.less(header_low_exceeded));
 wire total_exceeded=header_total_sum[32] || header_low_exceeded;
 wire command_header_ok=(frame_command<=3) &&
   ((frame_command==1 && frame_count>=1 && frame_count<=64) ||
    (frame_command!=1 && frame_count==0)) &&
   ((frame_command!=0 && frame_command!=3) || argument_complete==0) &&
   (frame_command!=2 || argument_complete!=0);
 wire[7:0] payload_offset=rx_position-8'd10;
 wire[6:0] payload_sample=payload_offset[7:1];
 wire rx_memory_write=rx_valid && rx_position>=10 && rx_position<138 &&
   rx_position[0] && frame_write_ok && payload_sample<frame_count;
 wire[8:0] input_write_address=frame_argument[8:0]+payload_sample;

 always @(posedge clk)begin
   if(!rst_n)begin
     rx_position<=0;frame_command<=0;frame_count<=0;frame_argument<=0;
     frame_faults<=0;rx_crc<=16'hffff;rx_crc_low<=0;sample_low<=0;
     rx_crc_steps<=0;frame_write_ok<=0;packet_event<=0;packet_write_ok<=0;
     packet_faults<=0;
   end else begin
     packet_event<=0;
     if(rx_crc_steps!=0)begin
       rx_crc<=rx_crc[0] ? (rx_crc>>1)^16'ha001:rx_crc>>1;
       rx_crc_steps<=rx_crc_steps-1'b1;
     end
     if(rx_framing)begin rx_position<=0;frame_write_ok<=0;end
     else if(rx_valid)begin
       if(rx_position<4)begin
         case(rx_position)
           0:rx_position<=rx_data==8'hd5 ? 1:0;
           1:rx_position<=rx_data==8'h49 ? 2:(rx_data==8'hd5 ? 1:0);
           2:rx_position<=rx_data==8'h31 ? 3:(rx_data==8'hd5 ? 1:0);
           3:if(rx_data==8'ha5)begin
               rx_position<=4;rx_crc<=16'hffff;rx_crc_steps<=0;
               frame_faults<=0;frame_write_ok<=0;
             end else rx_position<=rx_data==8'hd5 ? 1:0;
         endcase
       end else begin
         if(rx_position<138)begin rx_crc<=rx_crc^{8'd0,rx_data};rx_crc_steps<=8;end
         rx_position<=rx_position+1'b1;
         case(rx_position)
           4:frame_command<=rx_data;
           5:frame_count<=rx_data;
           6,7,8:frame_argument<={rx_data,frame_argument[31:8]};
           9:begin
             frame_argument<=argument_complete;
             frame_faults<=(!command_header_ok ? 16'h0080:16'd0) |
               ((frame_command==1 && argument_complete!={12'd0,admitted}) ? 16'h0002:16'd0) |
               ((frame_command==1 && header_level_sum>512) ? 16'h0004:16'd0) |
               (((frame_command==1 && (capture_exceeded || (started && total_exceeded))) ||
                 (frame_command==2 && argument_complete>CAPTURE_MAX)) ? 16'h0100:16'd0);
             frame_write_ok<=command_header_ok && frame_command==1 &&
               argument_complete=={12'd0,admitted} && header_level_sum<=512 && !run_fault &&
               !capture_exceeded && (!started || !total_exceeded);
           end
           138:rx_crc_low<=rx_data;
           139:begin
             packet_event<=1;packet_write_ok<=frame_write_ok;
             packet_faults<=frame_faults |
               ((rx_crc!={rx_data,rx_crc_low}) ? 16'h0001:16'd0);
             rx_position<=0;frame_write_ok<=0;
           end
           default:if(rx_position>=10 && rx_position<138)begin
             if(!rx_position[0])sample_low<=rx_data;
             if((frame_command!=1 || payload_sample>=frame_count) && rx_data!=0)
               frame_faults<=frame_faults|16'h0080;
           end
         endcase
       end
     end
   end
 end

 wire packet_valid=packet_event && packet_faults==0;
 // Finish the current UART frame before clearing its backing FIFO. A RESET
 // cannot truncate a packet or make its remaining sample bytes change meaning.
 // The host waits for the clean zero-counter status acknowledgement afterwards.
 wire reset_command=packet_valid && packet_command==0;
 reg reset_pending,tx_active;
 wire reset_request=reset_pending && !tx_active;
 always @(posedge clk)begin
   if(!rst_n)reset_pending<=0;
   else if(reset_request)reset_pending<=0;
   else if(reset_command)reset_pending<=1;
 end
 wire[32:0] committed_total_sum=header_total_sum;
 wire[10:0] committed_level_sum={1'b0,input_level}+{3'd0,packet_count};
 wire data_commit=packet_valid && packet_command==1 && packet_write_ok &&
   packet_argument=={12'd0,admitted} && committed_level_sum<=512 && !run_fault &&
   !capture_exceeded && (!started || !total_exceeded);
 wire[9:0] start_prefill=packet_argument<256 ? packet_argument[9:0]:10'd256;
 wire start_argument_less;
 digital_link_dsp_less32 start_compare_unit(.a(packet_argument),.b({12'd0,admitted}),.less(start_argument_less));
 wire start_accept=packet_valid && packet_command==2 && !started && !run_fault &&
   !start_argument_less && input_level>=start_prefill && packet_argument!=0 && packet_argument<=CAPTURE_MAX;

 // Original fractional sample clock, reset at START and never stalled by FIFO
 // occupancy. Starvation terminates an explicitly invalid run; it cannot resume.
 reg[SAMPLE_WIDTH-1:0] sample_accumulator;
 wire sample_tick_now=streaming && sample_accumulator>=CLK_HZ-32000;
 wire[SAMPLE_WIDTH-1:0] sample_accumulator_next;
 wire[SAMPLE_WIDTH-1:0] sample_delta=sample_tick_now ? SAMPLE_SUB:SAMPLE_ADD;
 digital_link_dsp_small_add #(.WIDTH(SAMPLE_WIDTH)) phase_sum_unit(
   .a(sample_accumulator),.b(sample_delta),.q(sample_accumulator_next));
 reg input_prefetch_valid,input_read_pending;
 wire[15:0] input_ram_q,output_ram_q;
 // WRITE_MODE=00 holds the DPB output on writes; these registers already
 // supply the one-word prefetch storage. No duplicate data register needed.
 wire signed[15:0] input_prefetch=input_ram_q;
 // A new read is issued on the next edge after sample_fire. auto_core consumes
 // in_valid on that edge and samples the OLD DPB Q before the nonblocking
 // RAM update. RX writes retain Q in WRITE_MODE00, so no holding copy is needed.
 assign in_data=input_prefetch;
 wire sample_fire=sample_tick_now && input_prefetch_valid && input_level!=0 &&
                  core_ready && !run_fault;
 wire input_read_request=!input_prefetch_valid && !input_read_pending && input_level!=0;
 wire input_read_fire=input_read_request && !rx_memory_write;

 // TX snapshots only the 80-byte header; sample payload stays in the output
 // ring. Selecting one bit at a time follows V18 status_auto and avoids an
 // eight-parallel-byte mux. CRC is advanced on each transmitted payload bit.
 // Only these four UART diagnostics saturate to uint8. All core counters,
 // fault decisions and processing remain uint32; transmitted slots stay uint32.
 function[7:0] diagnostic_u8_saturated;
   input[31:0] value;
   begin diagnostic_u8_saturated=(|value[31:8]) ? 8'hff:value[7:0];end
 endfunction

 reg[639:0] tx_header;
 reg status_pending;
 reg[7:0] tx_byte;
 reg[3:0] tx_phase;
 reg[$clog2(UART_DIV)-1:0] tx_baud_count;
 reg[6:0] tx_sample_count;
 reg[15:0] tx_crc;
 reg[STATUS_WIDTH-1:0] status_timer;
 wire[STATUS_WIDTH-1:0] status_timer_next;
 reg output_prefetch_valid,output_read_pending;
 wire[15:0] output_prefetch=output_ram_q;
 wire[7:0] tx_payload_offset=tx_byte-8'd80;
 wire tx_used_sample=tx_byte>=80 && tx_byte<208 &&
                     tx_payload_offset<{tx_sample_count,1'b0};
 wire tx_pop=tx_active && tx_used_sample && tx_byte[0] && tx_phase==9 &&
             tx_baud_count==0 && output_prefetch_valid;
 wire output_ready_block=output_level>=64 || (complete && output_level!=0);
 wire[6:0] next_block_count=output_level>=64 ? 7'd64:{1'b0,output_level[5:0]};
 wire next_is_data=output_ready_block;
 wire[7:0] next_flags={4'd0,started,run_fault,complete,streaming};
 wire next_frame=!tx_active && !reset_pending && !reset_command && !core_reset &&
                 (next_is_data || status_pending || status_timer==STATUS_CYCLES);
 wire output_read_request=tx_active && tx_sample_count!=0 &&
   (tx_byte<80 || tx_used_sample) && !output_prefetch_valid && !output_read_pending;
 // In every valid started run, received<=total and complete iff received==total.
 // The last accepted output sets complete; any fault latches invalid until RESET.
 wire output_accept=out_valid && !core_reset && started && !run_fault && !complete &&
   out_seq=={12'd0,output_received} && (output_level<512 || tx_pop);
 wire output_read_fire=output_read_request && !output_accept;
 wire tx_header_bit=tx_header[{tx_byte[6:0],tx_phase[2:0]}];
 wire tx_sample_bit=output_prefetch[{tx_byte[0],tx_phase[2:0]}];
 wire tx_crc_bit=tx_crc[{tx_byte[0],tx_phase[2:0]}];
 wire tx_data_bit=tx_byte<80 ? tx_header_bit:
                  tx_byte>=208 ? tx_crc_bit:(tx_used_sample ? tx_sample_bit:1'b0);
 wire tx_sample_missing=tx_active && tx_used_sample && tx_phase==0 &&
                       tx_baud_count==0 && !output_prefetch_valid;

 // One explicit DPB supplies the two physical ports. RX writes win over
 // input prefetch; output writes win over TX prefetch. Read responses are
 // registered and captured the following cycle; no asynchronous array reads.
 wire[9:0] ram_address_a={1'b0,(rx_memory_write ? input_write_address:consumed[8:0])};
 wire[9:0] ram_address_b={1'b1,(output_accept ? output_received[8:0]:output_transmitted[8:0])};
 digital_link_ram fifo_ram(.clk(clk),
   .write_a(rx_memory_write),.read_a(input_read_fire),.address_a(ram_address_a),
   .data_a({rx_data,sample_low}),.q_a(input_ram_q),
   .write_b(output_accept),.read_b(output_read_fire),.address_b(ram_address_b),
   .data_b(out_data),.q_b(output_ram_q));
 always @(posedge clk)begin
   if(!rst_n || reset_request)begin
     input_prefetch_valid<=0;input_read_pending<=0;
     output_prefetch_valid<=0;output_read_pending<=0;
   end else begin
     input_read_pending<=input_read_fire;
     output_read_pending<=output_read_fire;
     if(input_read_pending)input_prefetch_valid<=1;
     if(sample_fire)input_prefetch_valid<=0;
     if(output_read_pending)output_prefetch_valid<=1;
     if(tx_pop)output_prefetch_valid<=0;
   end
 end

 reg[15:0] fault_events;
 always @*begin
   fault_events=packet_event ? packet_faults:16'd0;
   if(rx_framing)fault_events[9]=1;
   if(packet_valid && packet_command==3)fault_events[10]=1;
   if(packet_valid && packet_command==2 && !start_accept)begin
     fault_events[7]=1;
     if(start_argument_less || packet_argument>CAPTURE_MAX)fault_events[8]=1;
   end
   if(packet_valid && packet_command==1 && !run_fault)begin
     if(packet_argument!={12'd0,admitted})fault_events[1]=1;
     if(committed_level_sum>512)fault_events[2]=1;
     if(capture_exceeded || (started && total_exceeded))fault_events[8]=1;
   end
   if(sample_tick_now && !run_fault)begin
     if(input_level==0 || !input_prefetch_valid)fault_events[3]=1;
     if(!core_ready)fault_events[5]=1;
   end
   if(out_valid && !core_reset && !run_fault)begin
     if(!started || out_seq!={12'd0,output_received})fault_events[6]=1;
     if(started && complete)fault_events[8]=1;
     if(output_level==512 && !tx_pop)fault_events[4]=1;
   end
   if(tx_sample_missing)fault_events[6]=1;
 end

 wire[32:0] consumed_sum,output_received_sum,output_transmitted_sum;
 wire[19:0] consumed_next=consumed_sum[19:0];
 wire[19:0] output_received_next=output_received_sum[19:0];
 digital_link_dsp_add33 consumed_sum_unit(.a({12'd0,consumed}),.b(32'd1),.q(consumed_sum));
 digital_link_dsp_add33 output_received_sum_unit(.a({12'd0,output_received}),.b(32'd1),.q(output_received_sum));
 digital_link_dsp_add33 output_transmitted_sum_unit(.a({12'd0,output_transmitted}),.b(32'd1),.q(output_transmitted_sum));
 wire[6:0] admitted_step=packet_count[6:0]-{6'd0,sample_fire};
 wire[9:0] input_step=data_commit ? {3'd0,admitted_step}:10'h3ff;
 wire[9:0] output_step={{9{tx_pop}},1'b1};
 wire[9:0] input_level_next,output_level_next;
 digital_link_dsp_packed_levels #(.STATUS_WIDTH(STATUS_WIDTH)) levels_sum_unit(
   .input_level(input_level),.output_level(output_level),.status_timer(status_timer),
   .input_step(input_step),.output_step(output_step),
   .input_next(input_level_next),.output_next(output_level_next),.status_next(status_timer_next));
 always @(posedge clk)begin
   if(!rst_n || reset_request)begin
     core_reset<=1;in_valid<=0;
     link_faults<=0;core_failed<=0;started<=0;streaming<=0;complete<=0;
     admitted<=0;consumed<=0;total<=0;output_received<=0;output_transmitted<=0;
     input_level<=0;output_level<=0;sample_accumulator<=0;
   end else begin
     core_reset<=0;in_valid<=0;
     if(core_fault && !core_reset)core_failed<=1;
     link_faults<=link_faults|fault_events;
     if(run_fault || fault_events!=0)begin streaming<=0;complete<=0;end
     if(!streaming)sample_accumulator<=0;
     else sample_accumulator<=sample_accumulator_next;
     // START defines the core's deterministic epoch after arbitrary host
     // prefill time. FIFO ownership is retained; first sample is 1563 clocks
     // later, so the synchronous core reset settles before input begins.
     if(start_accept)begin
       started<=1;streaming<=1;total<=packet_argument[19:0];core_reset<=1;
     end
     if(data_commit)admitted<=header_total_sum[19:0];
     if(data_commit || sample_fire)input_level<=input_level_next;
     if(sample_fire)begin
       in_valid<=1;consumed<=consumed_next;
       if(consumed_next==total)streaming<=0;
     end
     if(output_accept!=tx_pop)output_level<=output_level_next;
     if(output_accept)begin
       output_received<=output_received_next;
       if(output_received_next==total && fault_events==0)complete<=1;
     end
     if(tx_pop)output_transmitted<=output_transmitted_sum[19:0];
   end
 end

 always @(posedge clk)begin
   if(!rst_n || reset_request)begin
     tx<=1;tx_active<=0;tx_header<=0;tx_byte<=0;tx_phase<=0;tx_baud_count<=0;
     tx_sample_count<=0;tx_crc<=16'hffff;status_pending<=1;status_timer<=0;
   end else begin
     if(!tx_active && status_timer!=STATUS_CYCLES)status_timer<=status_timer_next;
     if(next_frame)begin
       tx_header<={16'd3,{7'd0,task_running},{6'd0,waiting},authorized_frequencies,
         max_fir,{6'd0,output_level},{6'd0,input_level},{1'b0,groups},error_code,active,
         link_faults,{24'd0,diagnostic_u8_saturated(saturated)},
         {24'd0,diagnostic_u8_saturated(sequence_faults)},
         {24'd0,diagnostic_u8_saturated(deadline)},
         {24'd0,diagnostic_u8_saturated(numeric)},dropped,frames,commits,
         {12'd0,admitted},{12'd0,consumed},{12'd0,output_transmitted},next_flags,CAPACITY_BYTE,
         (next_is_data ? {1'b0,next_block_count}:8'd0),(next_is_data ? 8'h81:8'h80),32'ha5314fd5};
       tx_sample_count<=next_is_data ? next_block_count:7'd0;
       tx_active<=1;tx_byte<=0;tx_phase<=0;tx_baud_count<=UART_DIV-1;
       tx_crc<=16'hffff;tx<=0;status_pending<=0;status_timer<=0;
     end else if(tx_active)begin
       if(tx_baud_count!=0)tx_baud_count<=tx_baud_count-1'b1;
       else begin
         tx_baud_count<=UART_DIV-1;
         if(tx_phase<8)begin
           tx<=tx_data_bit;tx_phase<=tx_phase+1'b1;
           if(tx_byte>=4 && tx_byte<208)
             tx_crc<=(tx_crc>>1)^((tx_crc[0]^tx_data_bit) ? 16'ha001:16'd0);
         end else if(tx_phase==8)begin tx<=1;tx_phase<=9;end
         else if(tx_byte==209)begin tx<=1;tx_active<=0;status_timer<=0;end
         else begin tx<=0;tx_byte<=tx_byte+1'b1;tx_phase<=0;end
       end
     end
     // Queue command acknowledgement after the controller has changed state.
     if(packet_valid && (packet_command==2 || packet_command==3 ||
                         (packet_command==1 && !started)))status_pending<=1;
   end
 end
endmodule
