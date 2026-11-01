`timescale 1ns/1ps
// Local auxiliary correction only. Snapshot prior strong authorizations at start.
// Keep exact original logarithm/interpolation; no full-band detection/median/top5.
module aux_peak_tracker #(parameter MAX_CENTERS=4,parameter LOG_FILE="rom/aux_log2_pair36.mem")(
 input clk,input rst_n,input load_valid,input[9:0] load_bin,input[63:0] load_power,
 input start,input[31:0] frame_seq,input[31:0] window_end,
 input[MAX_CENTERS-1:0] authorized_mask,input[32*MAX_CENTERS-1:0] authorized_frequencies,
 output reg busy,output reg done,output reg cache_invalid,output reg[3:0] error_code,
 output reg[31:0] cycles,output reg[31:0] rejected_loads,
 output reg cand_valid,output reg[2:0] cand_rank,output reg[10:0] cand_grid,
 output reg[31:0] cand_fcw,output reg[31:0] cand_amplitude,output reg[63:0] cand_power,
 output reg[31:0] out_frame_seq,output reg[31:0] out_window_end,output reg[2:0] cand_count
);
 localparam[63:0] MIN_POWER=64'd353082210785847;
 localparam signed[31:0] ZERO_LOG=-32'sd174725657;
 localparam[4:0] IDLE=0,SLOT=1,SCAN_SET=2,SCAN_PREV=3,SCAN_CENTER=4,SCAN_NEXT=5,SCAN_ADV=6,
  LOG_PREP=7,LOG_NORMALIZE=8,LOG_REQUEST=9,LOG_MUL=10,LOG_WAIT=11,LOG_STORE=12,
  OFFSET_PREP=13,DIV_LOOP=14,DIV_DONE=15,EMIT=16,NEXT_SLOT=17,FINISH=18,FAILED=19;
 reg[4:0] state;reg[9:0] current_bin,expected_bin,scan_hi,best_bin;
 reg loaded_complete,load_error,read_best,best_valid;reg[63:0] best_power;
 reg[31:0] seq_hold,end_hold;reg[MAX_CENTERS-1:0] snapshot_mask;
 reg[10:0] snapshot_grid[0:MAX_CENTERS-1];reg[2:0] slot;
 wire[10:0] center_grid=snapshot_grid[slot<MAX_CENTERS ? slot:0];
 wire[8:0] center_bin={1'b0,center_grid[10:3]}+
  (center_grid[2:0]>3'd4 || (center_grid[2:0]==3'd4 && center_grid[3]));
 reg[63:0] raw[0:511] /* synthesis syn_ramstyle="block_ram" */;
 reg[63:0] raw_q;reg[9:0] read_addr;
 (* syn_romstyle="block_rom" *) reg[35:0] log_pairs[0:255];
 reg[35:0] log_pair_q;reg[5:0] log_upper_lo_q,log_upper_hi_q;
 wire[23:0] log_lo_q={log_upper_lo_q,log_pair_q[17:0]};
 wire[23:0] log_hi_q={log_upper_hi_q,log_pair_q[35:18]};
 initial $readmemb(LOG_FILE,log_pairs);
 reg[63:0] previous_power,peak_power,next_power,log_power;
 reg[1:0] log_index;reg[63:0] log_normalized;reg[5:0] log_exponent;
 reg signed[31:0] log_left,log_center,log_right;reg[5:0] log_exponent_hold;reg[23:0] log_base_hold;
 reg[31:0] mul_a,mul_b;reg[63:0] product;
 function [5:0] log_upper;
 input [8:0] address;
 begin
   if(address>9'd256)log_upper=0;
   else if(address>=9'd123)begin
     if(address>=9'd194)begin
       if(address>=9'd235)begin
         if(address>=9'd256)log_upper=16;else log_upper=15;
       end else if(address>=9'd214)log_upper=14;else log_upper=13;
     end else if(address>=9'd157)begin
       if(address>=9'd175)log_upper=12;else log_upper=11;
     end else if(address>=9'd139)log_upper=10;else log_upper=9;
   end else if(address>=9'd49)begin
     if(address>=9'd76)begin
       if(address>=9'd107)log_upper=8;
       else if(address>=9'd91)log_upper=7;else log_upper=6;
     end else if(address>=9'd62)log_upper=5;else log_upper=4;
   end else if(address>=9'd24)begin
     if(address>=9'd36)log_upper=3;else log_upper=2;
   end else if(address>=9'd12)log_upper=1;else log_upper=0;
 end
 endfunction
 wire [7:0] log_address=log_normalized[62:55];
 wire [11:0] log_fraction=log_normalized[54:43];
 wire [63:0] log_interpolation_base=product>>12;
 wire [63:0] log_interpolation=log_interpolation_base+
   (product[11:0]>12'h800 || (product[11:0]==12'h800 && log_interpolation_base[0]));
 wire signed [31:0] log_value=$signed({4'b0,log_exponent_hold,22'b0})+
    $signed({8'b0,log_base_hold})+$signed(log_interpolation[31:0]);
 wire signed [32:0] log_denominator=($signed({log_center[31],log_center})<<<1)-
    $signed({log_left[31],log_left})-$signed({log_right[31],log_right});
 wire signed [33:0] log_numerator=($signed({{2{log_right[31]}},log_right})-
    $signed({{2{log_left[31]}},log_left}))<<<2;
 wire [33:0] numerator_magnitude=log_numerator[33] ? -log_numerator:log_numerator;
 reg [31:0] division_number,division_den,division_quotient;
 reg [32:0] division_rem;reg [5:0] division_bit;reg offset_negative;
 reg signed [3:0] grid_offset;
 wire [32:0] division_trial={division_rem[31:0],division_number[31]};
 wire [33:0] division_difference={1'b0,division_trial}-{2'b0,division_den};
 wire division_take=!division_difference[33];
 wire [32:0] division_next_rem=division_take ? division_difference[32:0]:division_trial;
 wire [33:0] double_division_rem={division_rem,1'b0};
 wire [32:0] division_rounded={1'b0,division_quotient}+
   (double_division_rem>{2'b0,division_den} || (double_division_rem=={2'b0,division_den} && division_quotient[0]));

 integer i;
 always @* begin
   read_addr=current_bin;
   case(state)
     SCAN_SET:read_addr=current_bin-1'b1;
     SCAN_PREV:read_addr=current_bin;
     SCAN_CENTER:read_addr=current_bin+1'b1;
     default:begin end
   endcase
 end
 wire[8:0] port_a_addr=load_valid && !busy ? load_bin[8:0]:read_addr[8:0];
 always @(posedge clk)begin
   raw_q<=raw[port_a_addr];
   if(load_valid && !busy && rst_n && load_bin<512)raw[port_a_addr]<=load_power;
   log_pair_q<=log_pairs[log_address];
   log_upper_lo_q<=log_upper({1'b0,log_address});log_upper_hi_q<=log_upper({1'b0,log_address}+1'b1);
   product<=mul_a*mul_b;
 end
 // synthesis translate_off
 always @(posedge clk)if(rst_n && start)
   for(i=0;i<MAX_CENTERS;i=i+1)if(authorized_mask[i] &&
      (authorized_frequencies[i*32+:19]!==19'd0 || authorized_frequencies[i*32+30+:2]!==2'd0))
      $fatal(1,"Aux tracker requires original AUTO frequency grid");
 // synthesis translate_on
 always @(posedge clk)begin
   done<=0;cand_valid<=0;
   if(!rst_n)begin
     busy<=0;state<=IDLE;cache_invalid<=0;error_code<=0;cycles<=0;rejected_loads<=0;
     current_bin<=32;expected_bin<=0;loaded_complete<=0;load_error<=0;seq_hold<=0;end_hold<=0;
     scan_hi<=32;best_bin<=0;read_best<=0;best_valid<=0;best_power<=0;snapshot_mask<=0;slot<=0;
     previous_power<=0;peak_power<=0;next_power<=0;log_power<=0;log_index<=0;log_normalized<=0;log_exponent<=0;
     log_left<=0;log_center<=0;log_right<=0;log_exponent_hold<=0;log_base_hold<=0;
     mul_a<=0;mul_b<=0;division_number<=0;division_den<=0;division_quotient<=0;division_rem<=0;division_bit<=0;
     offset_negative<=0;grid_offset<=0;cand_rank<=0;cand_grid<=0;cand_fcw<=0;cand_amplitude<=0;cand_power<=0;
     out_frame_seq<=0;out_window_end<=0;cand_count<=0;
     for(i=0;i<MAX_CENTERS;i=i+1)snapshot_grid[i]<=0;
   end else begin
     if(busy)cycles<=cycles+1'b1;
     if(load_valid && busy)rejected_loads<=rejected_loads+1'b1;
     if(load_valid && !busy)begin
       if(load_bin==0)begin expected_bin<=1;loaded_complete<=0;load_error<=0;end
       else if(load_bin!=expected_bin || load_bin>512)begin load_error<=1;loaded_complete<=0;end
       else if(load_bin==512)begin expected_bin<=513;loaded_complete<=1;end
       else expected_bin<=expected_bin+1'b1;
     end
     case(state)
       IDLE:if(start)begin
         busy<=1;cycles<=1;cache_invalid<=0;error_code<=0;seq_hold<=frame_seq;end_hold<=window_end;
         snapshot_mask<=authorized_mask;slot<=0;cand_count<=0;loaded_complete<=0;
         for(i=0;i<MAX_CENTERS;i=i+1)snapshot_grid[i]<=authorized_frequencies[i*32+19+:11];
         if(!loaded_complete || load_error || load_valid)begin error_code<=1;state<=FAILED;end else state<=SLOT;
       end
       SLOT:begin
         if(slot>=MAX_CENTERS)state<=FINISH;
         else if(!snapshot_mask[slot] || center_bin<32 || center_bin>224)state<=NEXT_SLOT;
         else begin
           current_bin<=center_bin>32 ? center_bin-1'b1:10'd32;
           scan_hi<=center_bin<224 ? center_bin+1'b1:10'd224;
           best_valid<=0;best_power<=0;read_best<=0;state<=SCAN_SET;
         end
       end
       SCAN_SET:state<=SCAN_PREV;
       SCAN_PREV:begin previous_power<=raw_q;state<=SCAN_CENTER;end
       SCAN_CENTER:begin peak_power<=raw_q;state<=SCAN_NEXT;end
       SCAN_NEXT:begin
         next_power<=raw_q;
         if(read_best)begin log_index<=0;log_power<=previous_power;state<=LOG_PREP;end
         else begin
           if(peak_power>previous_power && peak_power>=raw_q && peak_power>=MIN_POWER &&
              (!best_valid || peak_power>best_power))begin best_power<=peak_power;best_bin<=current_bin;best_valid<=1;end
           state<=SCAN_ADV;
         end
       end
       SCAN_ADV:begin
         if(current_bin<scan_hi)begin current_bin<=current_bin+1'b1;state<=SCAN_SET;end
         else if(best_valid)begin current_bin<=best_bin;read_best<=1;state<=SCAN_SET;end
         else state<=NEXT_SLOT;
       end
       LOG_PREP:begin
         if(log_power==0)begin
           case(log_index)0:log_left<=ZERO_LOG;1:log_center<=ZERO_LOG;2:log_right<=ZERO_LOG;endcase
           if(log_index==2)state<=OFFSET_PREP;
           else begin log_index<=log_index+1'b1;log_power<=log_index==0 ? peak_power:next_power;state<=LOG_PREP;end
         end else begin log_normalized<=log_power;log_exponent<=63;state<=LOG_NORMALIZE;end
       end
       // Exact normalization: replace the wide variable shifter/priority
       // encoder by at most seven byte shifts and seven bit shifts. No
       // nonzero bit is discarded; the Q22 log lookup is unchanged.
       LOG_NORMALIZE:begin
         if(log_normalized[63:56]==0)begin
           log_normalized<=log_normalized<<8;log_exponent<=log_exponent-6'd8;
         end else if(!log_normalized[63])begin
           log_normalized<=log_normalized<<1;log_exponent<=log_exponent-1'b1;
         end else state<=LOG_REQUEST;
       end
       LOG_REQUEST:state<=LOG_MUL;
       LOG_MUL:begin
         log_exponent_hold<=log_exponent;log_base_hold<=log_lo_q;
         mul_a<={8'b0,(log_hi_q-log_lo_q)};mul_b<={20'b0,log_fraction};state<=LOG_WAIT;
       end
       LOG_WAIT:state<=LOG_STORE;
       LOG_STORE:begin
         case(log_index)0:log_left<=log_value;1:log_center<=log_value;2:log_right<=log_value;endcase
         if(log_index==2)state<=OFFSET_PREP;
         else begin log_index<=log_index+1'b1;log_power<=log_index==0 ? peak_power:next_power;state<=LOG_PREP;end
       end
       OFFSET_PREP:begin
         if(log_denominator<=0 || log_denominator>33'sd4294967295 ||
            log_numerator>34'sd4294967295 || log_numerator < -34'sd4294967295)begin error_code<=3;state<=FAILED;end
         else begin
           offset_negative<=log_numerator[33];division_number<=numerator_magnitude[31:0];
           division_den<=log_denominator[31:0];division_quotient<=0;division_rem<=0;division_bit<=0;state<=DIV_LOOP;
         end
       end
       DIV_LOOP:begin
         division_number<=division_number<<1;division_rem<=division_next_rem;division_quotient<={division_quotient[30:0],division_take};
         if(division_bit==31)state<=DIV_DONE;else division_bit<=division_bit+1'b1;
       end

       DIV_DONE:begin
         if(division_rounded>4)grid_offset<=offset_negative ? -4'sd4:4'sd4;
         else grid_offset<=offset_negative ? -$signed(division_rounded[3:0]):$signed(division_rounded[3:0]);
         state<=EMIT;
       end
       EMIT:begin
         cand_valid<=1;cand_rank<=cand_count;cand_grid<=({1'b0,current_bin}<<3)+{{7{grid_offset[3]}},grid_offset};
         cand_fcw<=((({21'd0,current_bin}<<3)+{{28{grid_offset[3]}},grid_offset})<<19);
         cand_power<=peak_power;cand_amplitude<=0; // unused amplitude telemetry; no sqrt datapath
         out_frame_seq<=seq_hold;out_window_end<=end_hold;cand_count<=cand_count+1'b1;state<=NEXT_SLOT;
       end
       NEXT_SLOT:begin slot<=slot+1'b1;state<=SLOT;end
       FINISH:begin done<=1;busy<=0;state<=IDLE;out_frame_seq<=seq_hold;out_window_end<=end_hold;end
       FAILED:begin cache_invalid<=1;done<=1;busy<=0;cand_count<=0;state<=IDLE;out_frame_seq<=seq_hold;out_window_end<=end_hold;end
       default:begin error_code<=4;state<=FAILED;end
     endcase
   end
 end
endmodule
