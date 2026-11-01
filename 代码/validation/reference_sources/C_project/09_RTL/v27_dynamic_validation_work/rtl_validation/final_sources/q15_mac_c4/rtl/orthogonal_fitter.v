`timescale 1ns/1ps
// v12 EXPERIMENT. Trial score is residual correlation power, using the
// latest complete joint amplitudes. Refit ALL selected centers immediately
// after each coordinate update; retain three sweeps and raw strong admission.
// No online true frequency/clean target. This changes the v7 search algorithm.
module orthogonal_fitter #(parameter MAX_CENTERS=5)(
 input clk,input rst_n,input fft_valid,input [8:0] fft_bin,
 input signed [31:0] fft_real,input signed [31:0] fft_imag,
 input start,input [31:0] frame_seq,
 output reg busy,output reg done,output reg error,output reg [7:0] error_code,
 output reg [31:0] last_cycles,output reg [31:0] result_frame_seq,
 output reg out_valid,output reg [10:0] grid_abs,output reg [79:0] amplitude_square,
 output reg [15:0] trial_count,output reg [7:0] search_count,output reg [7:0] refit_count,
 output reg [15:0] rhs_count,
 output reg capacity_reached,output reg overflow_candidate
);
 localparam OTHERS=MAX_CENTERS<5 ? MAX_CENTERS:MAX_CENTERS-1;
 localparam LOWER_WORDS=OTHERS*(OTHERS-1)/2,FC_WORDS=LOWER_WORDS+3*OTHERS;
 localparam FC_STRIDE=FC_WORDS+2,FC_TUPLE_WIDTH=3+11*OTHERS;
 reg probe_pending,probe_report;reg[10:0] probe_grid;reg[63:0] probe_power;
 localparam [7:0] IDLE=0,INIT=1,PEAK0=2,PEAK1=3,PEAK2=4,PEAK3=5,
 PREP=6,LOADG=7,STOREG=8,FSTART=9,FWAIT=10,META0=11,META1=12,
 RLOAD0=13,RLOAD1=14,SSTART=15,SWAIT=16,SPOST=17,
 TRIAL0=18,TRIAL1=19,TRIAL2=20,TRIALG=21,TRIALGS=22,FWD0=23,FWD1=24,
 WEIGHT0=25,NORM0=26,NORM1=27,DELTA0=28,DELTA1=29,POWER0=30,POWER1=31,
 SCORE0=32,SCORE1=33,TRIALNEXT=34,BEST0=35,NEW0=36,NEW1=37,
 COORD0=38,COORD1=39,JOINT0=40,JOINT1=41,RCLEAR=42,
 FINAL0=43,FINAL1=44,SIDE0=45,SIDE1=46,SIDE2=47,SQRT0=48,SQRT1=49,
 SORT0=50,SORT1=51,FINAL2=52,FINAL3=53,EMIT=54,FINISH=55,FAIL=56,
 GWAIT1=60,GWAIT2=61,RHS0=62,RHS1=63,RHSSET=64,RHSWAIT=65,RHSISSUE=66,RHSDRAIN=67,RHSHIT0=68,RHSHIT1=69,
 RES0=70,RES1=71,RES2=72,REST0=73,REST1=74,REST2=75,RESADD=76,RESEND=77,
 DOT=80,DRAIN=81,GBATCH=143,GDRAIN=144,FWD_PREP=145,
 COMP_SEED=146,COMP_ADJ=147,COMP_EXPAND=148,COMP_PACK=149,
 FCLOOKUP=150,FCLOAD=151,FCDRAIN=152,FCSAVE=153,FCSAVED=154,COMBREQ=155,COMBDRAIN=156,SCOREHI1=157,
 WORKREAD=158,WORKWAIT=159,COPY0=160,COPY1=161,COPY2=162,FCSAVEW=163,
 MEDIAN=164,SORTSET=165,SORTWAIT=166,FCWAIT=167,FCMATCH=168;
 localparam [3:0] DFWD=1,DWEIGHT=2,DNORM=3,DDELTA=4,DPOWER=5,DRES=6,DSCORE=7,DSCOREHI=8,DSCORELO=9;
 localparam [63:0] EARLY_POWER=64'd344806846471,OUTER_POWER=64'd851461804550;
 localparam[7:0] MCOPY0=169,MCOPY1=170,MCOPY2=171;
 reg[2:0] selected_source[0:MAX_CENTERS-1];
 reg [7:0] state,return_rhs,return_res,return_dot;
 reg [31:0] cycles;
 reg [10:0] clear_index;
 reg [8:0] ram_addr,rv_addr,res_waddr;
 reg [10:0] cache_addr,cache_waddr;
 reg res_we,rv_we,cache_we,cv_we;reg [4:0] rv_wdata,res_generation;
 reg [8:0] cache_map_wdata;reg [9:0] cache_allocate;reg [8:0] cache_data_addr,cache_data_waddr;
 reg fc_raw_we;reg [8:0] fc_raw_waddr;reg signed [31:0] fc_raw_wdata;
 // Existing raw FFT bank has unused addresses377..511. Put the ten
 // coefficients, ten solver values and five80-bit final powers here.
 localparam [8:0] WCOEFF=9'd377,WSOL=WCOEFF+2*MAX_CENTERS,WPOWER=WSOL+2*MAX_CENTERS,
   WLOWER=WPOWER+2*MAX_CENTERS,WINVERSE=WLOWER+LOWER_WORDS,
   WCROSS=WINVERSE+OTHERS,WY=WCROSS+OTHERS,WZ=WY+OTHERS,WMAG=WZ+OTHERS,WFC=WMAG+18;
 reg work_we,work_read;reg [8:0] work_waddr,work_addr_a,work_addr_b;reg [63:0] work_wdata;
 wire [63:0] work_q;reg [7:0] work_return;reg [2:0] copy_i;reg copy_lane;
 wire [8:0] raw_port_a=fft_valid && fft_bin<=256 ? fft_bin:fc_raw_we ? fc_raw_waddr:work_read ? work_addr_a:ram_addr;
 wire [8:0] raw_port_b=work_we ? work_waddr:work_addr_b;
 reg [63:0] res_wdata;
 reg [55:0] cache_wdata;
 // Raw-bank P&R compatibility candidate. Supported normal write mode.
 // Own/cross-write read results are held; actual consumers need regression.
 wire[63:0] raw_q;
 wire raw_write_a=(fft_valid && fft_bin<=256) || fc_raw_we;
 wire raw_read_a=!raw_write_a && !(work_we && raw_port_a==raw_port_b);
 wire raw_read_b=!work_we && !(raw_write_a && raw_port_a==raw_port_b);
 wire[63:0] raw_write_data_a=fft_valid && fft_bin<=256 ?
   {fft_imag,fft_real}:{32'b0,fc_raw_wdata};
 raw_dpb64_q24 raw_bank(.clk(clk),.addr_a(raw_port_a),.addr_b(raw_port_b),
   .write_a(raw_write_a),.write_b(work_we),.read_a(raw_read_a),.read_b(raw_read_b),
   .data_a(raw_write_data_a),.data_b(work_wdata),.q_a(raw_q),.q_b(work_q));
 // Simulation-only shadow is not synthesized and never drives read outputs.
 // synthesis translate_off
 reg[63:0] raw_mem[0:511];
 always @(posedge clk)begin
   if(fft_valid && fft_bin<=256)raw_mem[raw_port_a]<={fft_imag,fft_real};
   else if(fc_raw_we)raw_mem[raw_port_a]<={32'b0,fc_raw_wdata};
   if(work_we)raw_mem[raw_port_b]<=work_wdata;
 end
 // synthesis translate_on
 reg [63:0] res_mem[0:511] /* synthesis syn_ramstyle="block_ram" */;
 reg [4:0] res_valid[0:511] /* synthesis syn_ramstyle="block_ram" */;
 reg [55:0] rhs_mem[0:511] /* synthesis syn_ramstyle="block_ram" */;
 // Transparent combinational write-address delay only: no new RAM port,
 // enable, register, read-address path or sample/configuration clock.
 (* syn_keep=1 *) wire [8:0] rhs_write_address_held;
 genvar rhs_hold_bit;
 generate for(rhs_hold_bit=0;rhs_hold_bit<9;rhs_hold_bit=rhs_hold_bit+1)begin:rhs_write_hold
   (* syn_keep=1,syn_preserve=1 *) LUT4 #(.INIT(16'hAAAA)) address_delay(
     .I0(cache_data_waddr[rhs_hold_bit]),.I1(1'b0),.I2(1'b0),.I3(1'b0),
     .F(rhs_write_address_held[rhs_hold_bit]));
 end endgenerate
 reg [8:0] rhs_map[0:2047] /* synthesis syn_ramstyle="block_ram" */;
 reg [63:0] res_q;reg [4:0] rv_q;
 reg [55:0] cache_q;reg [8:0] cache_map_q;
 reg [7:0] template_addr;reg signed [25:0] template_rom[0:207] /* synthesis syn_romstyle="block_rom" */;
 reg signed [25:0] template_q;
 reg [10:0] gram_addr;reg signed [31:0] gram_rom[0:1736] /* synthesis syn_romstyle="block_rom" */;
 reg signed [31:0] gram_q;
 reg batch_matrix;reg [2:0] batch_row,batch_col;reg [5:0] batch_index,batch_length;
 reg batch_valid0,batch_valid1,batch_negative0,batch_negative1,batch_zero0,batch_zero1;
 reg [5:0] batch_index0,batch_index1;reg [2:0] batch_row0,batch_col0,batch_row1,batch_col1;
 reg others_ready,cross_any;reg signed [31:0] saved_trial_rhs_re,saved_trial_rhs_im;
 reg signed [31:0] batch_value;
 initial begin
   $readmemb("rom/detector_template_q24.mem",template_rom);
   $readmemb("rom/detector_gram_compact_q26.mem",gram_rom);
 end
 always @(posedge clk) begin
   res_q<=res_mem[ram_addr];rv_q<=res_valid[rv_addr];
   cache_q<=rhs_mem[cache_data_addr];cache_map_q<=rhs_map[cache_addr];
   template_q<=template_rom[template_addr];gram_q<=gram_rom[gram_addr];
   if(res_we)res_mem[res_waddr]<=res_wdata;
   if(rv_we)res_valid[res_waddr]<=rv_wdata;
   if(cache_we)rhs_mem[rhs_write_address_held]<=cache_wdata;
   if(cv_we)rhs_map[cache_waddr]<=cache_map_wdata;
 end
 reg [2:0] count,selected_n,load_i,transaction,lane,meta_i,meta_j;
 reg [10:0] indices[0:MAX_CENTERS-1],selected[0:MAX_CENTERS-1];
 reg [10:0] original_selected[0:OTHERS-1],component_lo,component_hi;
 reg [2:0] original_n,component_i,component_j,component_step,component_pack_n;
 reg [OTHERS-1:0] component_members,component_adjacency[0:OTHERS-1];reg [OTHERS-1:0] component_next;
 // Per-frame factor/RHS memoization occupies existing raw RAM addresses
 //257..376. FFT writes only0..256; template/side reads never exceed256.
 reg [5:0] fc_valid;reg [2:0] fc_slot,fc_replace,fc_cursor;
 reg [4:0] fc_word;
 reg fc_read_valid0,fc_read_valid1;reg [4:0] fc_word0,fc_word1;
 reg signed [31:0] fc_save_data;
 wire [FC_TUPLE_WIDTH-1:0] fc_tuple;
 assign fc_tuple[FC_TUPLE_WIDTH-1 -:3]=selected_n;
 genvar tuple_member;generate for(tuple_member=0;tuple_member<OTHERS;tuple_member=tuple_member+1)begin:factor_tuple
   assign fc_tuple[tuple_member*11+:11]=selected_n>tuple_member ? selected[tuple_member]:11'd0;
 end endgenerate
 wire [8:0] fc_base=9'd257+fc_slot*FC_STRIDE;
 // Testbench-only names preserve trace readability; synthesis sees only
 // the synchronous dual-port RAM accesses below.
 // synthesis translate_off
 wire signed [31:0] coefficient_re[0:MAX_CENTERS-1],coefficient_im[0:MAX_CENTERS-1],sol_re[0:MAX_CENTERS-1],sol_im[0:MAX_CENTERS-1];
 genvar trace_member;
 generate for(trace_member=0;trace_member<MAX_CENTERS;trace_member=trace_member+1)begin:trace_work
   assign coefficient_re[trace_member]=raw_mem[WCOEFF+2*trace_member][31:0];
   assign coefficient_im[trace_member]=raw_mem[WCOEFF+1+2*trace_member][31:0];
   assign sol_re[trace_member]=raw_mem[WSOL+2*trace_member][31:0];
   assign sol_im[trace_member]=raw_mem[WSOL+1+2*trace_member][31:0];
 end endgenerate
 // synthesis translate_on
 // Others has at most4members; only6 strict-lower cells are needed. The
 // separate joint solver still factors the complete5x5 matrix.
 reg [2:0] coordinate,sweep,joint_purpose;
 reg coordinate_mode;reg [10:0] search_center,trial,trial_hi,best_index,rhs_index;
 reg [4:0] radius;reg [8:0] peak_bin,best_bin,res_bin;
 reg [63:0] best_peak_power;reg [63:0] current_power,best_power;
 reg signed [31:0] rhs_re,rhs_im,delta_re,delta_im,current_norm,best_norm,best_re,best_im;
 reg [7:0] projection_return;reg projection_valid;
 reg [2:0] forward_i,res_member;reg [4:0] rhs_tap;reg rhs_lane;
 reg [7:0] rhs_tag;
 reg signed [63:0] acc_re,acc_im,dot_result,dot_im_result,res_sum_re,res_sum_im;
 reg signed [31:0] residual_re,residual_im;
 reg signed [31:0] power_re,power_im;
 reg [3:0] dot_mode;reg [5:0] dot_length,dot_index;reg [2:0] dot_row;
 reg [95:0] score_left,score_right;
 reg [5:0] stream_index,stream_delta_end,stream_norm_end;
 reg stream_delta_ready;reg signed [63:0] stream_norm_sum,stream_power_sum;
 reg [2:0] final_i,emit_i;reg [4:0] side_i;reg [8:0] final_peak;
 reg [63:0] final_current_power;reg [MAX_CENTERS-1:0] final_mask,emitted_mask;
 reg [31:0] sort_value;reg [4:0] sort_j;
 reg [63:0] sqrt_work;reg [33:0] sqrt_rem;reg [31:0] sqrt_root;reg [5:0] sqrt_left;
 reg [31:0] median_q30;
 // Before each of the 32 restoring steps, remainder < 2*root+1.
 // Before the final step root fits31 bits, so trial fits34 bits. The final
 // remainder is never shifted again; the next root resets it to zero.
 wire [33:0] sqrt_trial={sqrt_rem[31:0],sqrt_work[63:62]};
 wire [33:0] sqrt_test={sqrt_root,2'b01};
 wire [34:0] sqrt_difference={1'b0,sqrt_trial}-{1'b0,sqrt_test};
 wire sqrt_take=!sqrt_difference[34];
 integer idx;
 reg [2:0] tmp_n;
 reg signed [13:0] tmp_delta,tmp_base,tmp_offset;
 reg [12:0] tmp_sum;
 reg invalid_trial,invalid_bin;
 reg signed [63:0] rounded;
 wire signed[63:0] round25_re,round25_im,round30_re,round30_im;
 function signed [63:0] rne;
 input signed [63:0] value;input integer shift;
 // Arithmetic-floor quotient plus ties-to-even increment. One extra sign
 // bit preserves carry when rounding the largest positive 64-bit input.
 // Fixed-width variable-carry add avoids the conditional +1/mux chain.
 reg signed [62:0] q2;
 reg signed [58:0] q6;
 reg signed [45:0] q19;
 reg signed [40:0] q24;
 reg signed [33:0] q31;
 begin
 case(shift)
 2:begin
   q2=$signed({value[63],value[63:2]})+{62'd0,(value[1] && ((|value[0:0]) || value[2]))};
   rne={{1{q2[62]}},q2};
 end
 6:begin
   q6=$signed({value[63],value[63:6]})+{58'd0,(value[5] && ((|value[4:0]) || value[6]))};
   rne={{5{q6[58]}},q6};
 end
 19:begin
   q19=$signed({value[63],value[63:19]})+{45'd0,(value[18] && ((|value[17:0]) || value[19]))};
   rne={{18{q19[45]}},q19};
 end
 24:begin
   q24=$signed({value[63],value[63:24]})+{40'd0,(value[23] && ((|value[22:0]) || value[24]))};
   rne={{23{q24[40]}},q24};
 end
 31:begin
   q31=$signed({value[63],value[63:31]})+{33'd0,(value[30] && ((|value[29:0]) || value[31]))};
   rne={{30{q31[33]}},q31};
 end
 default:rne=64'sd0;
 endcase
 end
 endfunction
 function [2:0] lower_address;
 input [2:0] row;input [5:0] col;
 begin case(row)3:lower_address=3+col;2:lower_address=1+col;default:lower_address=0;endcase end
 endfunction
 function [7:0] template_address;
 input [3:0] fraction;input [4:0] tap;
 reg [7:0] extended;
 begin extended={4'b0,fraction};template_address=(extended<<3)+(extended<<2)+extended+tap;end
 endfunction
 function [12:0] gram_lookup;
 input [10:0] a,b;reg [10:0] d;reg [3:0] f,r,o,s;reg [11:0] sum;
 begin
   if(a<=b)begin d=b-a;f=a[3:0];end else begin d=a-b;f=b[3:0];end
   r=f+d[4:1];if(d[0])s=4'd15-r;else s=4'd0-r;o=r<s ? r:s;sum={8'b0,f}+{1'b0,d};
   gram_lookup[12]=d>11'd192;gram_lookup[11]=sum[4];gram_lookup[10:0]=d<=11'd192 ? (d<<3)+d+o:11'd0;
 end
 endfunction
 wire [10:0] batch_a=batch_matrix ? selected[batch_row]:(batch_index==0 ? trial:selected[batch_index-1'b1]);
 wire [10:0] batch_b=batch_matrix ? selected[batch_col]:trial;
 wire [12:0] batch_lookup=gram_lookup(batch_a,batch_b);
 task rhs_read;
 input [10:0] index;input [7:0] next_state;
 begin rhs_index<=index;cache_addr<=index;return_rhs<=next_state;state<=RHS0;end
 endtask
 task residual_read;
 input [8:0] index;input [7:0] next_state;
 begin res_bin<=index;ram_addr<=index;rv_addr<=index;return_res<=next_state;state<=RES0;end
 endtask
 task dot;
 input [3:0] mode;input [5:0] length;input [7:0] next_state;
 begin dot_mode<=mode;dot_length<=length;dot_index<=0;acc_re<=0;acc_im<=0;
   score_left<=0;score_right<=0;return_dot<=next_state;
   if(length==0)begin dot_result<=0;dot_im_result<=0;state<=next_state;end else state<=DOT;
 end
 endtask
 task next_trial;
 begin if(trial<trial_hi)begin trial<=trial+1'b1;state<=TRIAL1;end else state<=BEST0;end
 endtask
 reg matrix_we,rhs_we,factor_start,solve_start,meta_read,meta_inverse;
 reg [2:0] matrix_row,matrix_col,solver_rhs_index,meta_row,meta_col;
 reg signed [31:0] matrix_data,solver_rhs_data;
 wire solver_busy,factor_valid,solver_done,solver_error,solver_result_valid,meta_valid,meta_ready;
 wire [3:0] solver_error_code;wire [2:0] solver_result_index;
 wire signed [31:0] solver_result,meta_data;
 wire mul_ready,mul_valid;wire signed [63:0] mul_product;wire [7:0] mul_tag;
 reg work_mac_valid;reg [1:0] work_mac_kind;reg [7:0] work_mac_tag;
 reg signed [31:0] work_mac_a,work_mac_b;
 reg ram_mul_issue;reg [1:0] ram_mul_kind;reg [7:0] ram_mul_tag;
 reg signed [31:0] ram_mul_a,ram_mul_b;
 always @* begin
   ram_mul_issue=0;ram_mul_kind=0;ram_mul_tag=dot_index;ram_mul_a=0;ram_mul_b=0;
   work_read=0;work_addr_a=ram_addr;work_addr_b=9'd0;
   if(state==DOT && dot_mode==DDELTA)begin
     ram_mul_issue=1;ram_mul_kind=3;work_read=1;
     work_addr_a=WCROSS+(dot_index>>1);work_addr_b=WSOL+dot_index;
   end else if(state==DOT && dot_mode==DRES)begin
     ram_mul_issue=1;ram_mul_kind=2;work_addr_b=WCOEFF+({6'b0,res_member}<<1)+dot_index[0];
     ram_mul_b={{6{template_q[25]}},template_q};
   end else if(state==WORKREAD || state==WORKWAIT)begin
     work_addr_b=ram_addr+1'b1;
   end
 end
 reg mul_request;reg signed [31:0] mul_a,mul_b;reg [7:0] request_tag;
 fitting_solver #(.MAX_CENTERS(MAX_CENTERS)) u_solver(.clk(clk),.rst_n(rst_n),.matrix_we(matrix_we),.matrix_row(matrix_row),.matrix_col(matrix_col),
 .matrix_data(matrix_data),.rhs_we(rhs_we),.rhs_index(solver_rhs_index),.rhs_data(solver_rhs_data),
 .factor_start(factor_start),.solve_start(solve_start),.n(selected_n),.busy(solver_busy),.factor_valid(factor_valid),
 .done(solver_done),.error(solver_error),.error_code(solver_error_code),.cycles(),.rejected_starts(),
 .result_valid(solver_result_valid),.result_index(solver_result_index),.result_data(solver_result),
 .ext_mul_valid(mul_request),.ext_mul_a(mul_a),.ext_mul_b(mul_b),.ext_mul_tag(request_tag),
 .ext_mul_ready(mul_ready),.ext_mul_out_valid(mul_valid),.ext_mul_product(mul_product),.ext_mul_out_tag(mul_tag),
 .meta_read(meta_read),.meta_inverse(meta_inverse),.meta_row(meta_row),.meta_col(meta_col),
 .meta_valid(meta_valid),.meta_data(meta_data),.meta_ready(meta_ready));
 always @* begin
   mul_request=0;mul_a=0;mul_b=0;request_tag=dot_index;
   if(work_mac_valid)begin
     mul_request=1;request_tag=work_mac_tag;
     mul_a=work_mac_kind==3 ? raw_q[31:0]:work_mac_kind==2 ? work_q[31:0]:work_mac_a;
     mul_b=work_mac_kind==3 ? work_q[31:0]:work_mac_b;
   end else if(state==DOT && dot_mode==DPOWER)begin
     mul_request=1;mul_a=dot_index[0] ? power_im:power_re;mul_b=mul_a;
   end else if(state==RHSISSUE)begin
     mul_request=1;request_tag=rhs_tag;mul_a={{6{template_q[25]}},template_q};
     mul_b=rhs_lane ? raw_q[63:32]:raw_q[31:0];
   end
 end
 // Share only mutually exclusive FSM operations. Fixed25/30-bit scales
 // stay separate so the arithmetic path has no variable-scale selection.
 // The tagged weight-product rounder remains independent: it can overlap.
 wire signed[63:0] sum_im_product=acc_im+mul_product;
 wire signed[63:0] round30_input_re=dot_result;
 wire signed[63:0] round30_input_im=dot_im_result;
 wire signed[63:0] round25_input_re=state==8'd142 ? res_sum_re:acc_re;
 wire signed[63:0] round25_input_im=state==8'd142 ? res_sum_im:sum_im_product;
 assign round30_re=rne(round30_input_re,24);
 assign round30_im=rne(round30_input_im,24);
 assign round25_re=state==8'd142 ? rne(round25_input_re,19):rne(round25_input_re,31);
 assign round25_im=state==8'd142 ? rne(round25_input_im,19):rne(round25_input_im,31);
 always @(posedge clk)begin
   out_valid<=0;overflow_candidate<=0;done<=0;matrix_we<=0;rhs_we<=0;factor_start<=0;solve_start<=0;meta_read<=0;
   res_we<=0;rv_we<=0;cache_we<=0;cv_we<=0;
   fc_raw_we<=0;work_we<=0;
   if(!rst_n)begin
     probe_pending<=0;probe_report<=0;probe_grid<=0;probe_power<=0;capacity_reached<=0;overflow_candidate<=0;
     state<=IDLE;busy<=0;done<=0;error<=0;error_code<=0;last_cycles<=0;cycles<=0;
     out_valid<=0;grid_abs<=0;amplitude_square<=0;result_frame_seq<=0;
     trial_count<=0;search_count<=0;refit_count<=0;rhs_count<=0;
     ram_addr<=0;rv_addr<=0;cache_addr<=0;template_addr<=0;gram_addr<=0;
     matrix_we<=0;rhs_we<=0;factor_start<=0;solve_start<=0;meta_read<=0;
     res_we<=0;rv_we<=0;cache_we<=0;cv_we<=0;
     batch_valid0<=0;batch_valid1<=0;
     fc_valid<=0;fc_read_valid0<=0;fc_read_valid1<=0;fc_raw_we<=0;work_we<=0;work_mac_valid<=0;
   end else begin
     work_mac_valid<=ram_mul_issue && mul_ready && !work_we;
     if(ram_mul_issue && mul_ready && !work_we)begin work_mac_kind<=ram_mul_kind;work_mac_tag<=ram_mul_tag;work_mac_a<=ram_mul_a;work_mac_b<=ram_mul_b;end
     batch_valid0<=state==GBATCH;batch_valid1<=batch_valid0;
     batch_negative1<=batch_negative0;batch_zero1<=batch_zero0;
     batch_index1<=batch_index0;batch_row1<=batch_row0;batch_col1<=batch_col0;
     fc_read_valid0<=state==FCLOAD;fc_read_valid1<=fc_read_valid0;fc_word1<=fc_word0;
     if(busy)cycles<=cycles+1'b1;
     if(solver_result_valid)begin work_we<=1;work_waddr<=WSOL+({6'b0,solver_result_index}<<1)+lane[0];work_wdata<={32'b0,solver_result};end
     case(state)
       IDLE:if(start)begin busy<=1;error<=0;error_code<=0;result_frame_seq<=frame_seq;
         cycles<=0;clear_index<=0;count<=0;probe_pending<=0;probe_report<=0;capacity_reached<=0;final_mask<=0;emitted_mask<=0;trial_count<=0;search_count<=0;refit_count<=0;rhs_count<=0;
         cache_allocate<=0;res_generation<=0;state<=INIT;end
       INIT:begin
         cache_waddr<=clear_index;cv_we<=1;cache_map_wdata<=9'd511;
         fc_valid<=0;fc_replace<=0;
         if(clear_index<257)begin res_waddr<=clear_index[8:0];rv_we<=1;rv_wdata<=31;end
         if(clear_index==1536)state<=PEAK0;else clear_index<=clear_index+1'b1;
       end
       PEAK0:begin peak_bin<=16;best_bin<=16;best_peak_power<=0;state<=PEAK1;end
       PEAK1:residual_read(peak_bin,PEAK2);
       PEAK2:begin power_re<=residual_re;power_im<=residual_im;dot(DPOWER,2,PEAK3);end
       PEAK3:begin
         invalid_bin=0;
         for(idx=0;idx<MAX_CENTERS;idx=idx+1)if(idx<count)begin
           tmp_delta=$signed({1'b0,peak_bin,4'b0})-$signed({1'b0,indices[idx]})-256;
           if(tmp_delta<0)tmp_delta=-tmp_delta;if(tmp_delta<=12)invalid_bin=1;
         end
         if(!invalid_bin && $unsigned(dot_result)>best_peak_power)begin best_peak_power<=dot_result[63:0];best_bin<=peak_bin;end
         if(peak_bin==112)begin search_center<=(((!invalid_bin && $unsigned(dot_result)>best_peak_power) ? {peak_bin,4'b0}:{best_bin,4'b0})-256);
           coordinate_mode<=0;radius<=12;projection_return<=NEW0;state<=PREP;
         end else begin peak_bin<=peak_bin+1'b1;state<=PEAK1;end
       end
       PREP:begin
         tmp_n=0;
         for(idx=0;idx<MAX_CENTERS;idx=idx+1)
           if(idx<count && (!coordinate_mode || idx!=coordinate))begin
             selected[tmp_n]<=indices[idx];selected_source[tmp_n]<=idx;
             tmp_n=tmp_n+1;
           end
         selected_n<=tmp_n;copy_i<=0;copy_lane<=0;
         if(tmp_n==0)state<=TRIAL0;else state<=MCOPY0;
       end
       MCOPY0:begin
         ram_addr<=WCOEFF+({6'b0,selected_source[copy_i]}<<1)+copy_lane;
         state<=MCOPY1;
       end
       MCOPY1:state<=MCOPY2;
       MCOPY2:begin
         work_we<=1;work_waddr<=WSOL+({6'b0,copy_i}<<1)+copy_lane;work_wdata<=raw_q;
         if(!copy_lane)begin copy_lane<=1;state<=MCOPY0;end
         else if(copy_i+1<selected_n)begin copy_i<=copy_i+1'b1;copy_lane<=0;state<=MCOPY0;end
         else state<=TRIAL0;
       end
       LOADG:begin batch_matrix<=1;batch_row<=0;batch_col<=0;batch_index<=0;
         batch_length<=selected_n*selected_n; state<=GBATCH;end
       FSTART:begin factor_start<=1;state<=FWAIT;end
       FWAIT:if(solver_done)begin load_i<=0;lane<=0;state<=RLOAD0;end
       RLOAD0:begin
         if(transaction==2)begin rhs_re<=best_re;rhs_im<=best_im;state<=RLOAD1;end
         else rhs_read(selected[load_i],RLOAD1);
       end
       RLOAD1:begin rhs_we<=1;solver_rhs_index<=load_i;solver_rhs_data<=lane==0 ? rhs_re:rhs_im;
         if(load_i+1<selected_n)begin load_i<=load_i+1'b1;state<=RLOAD0;end else state<=SSTART;
       end
       SSTART:begin solve_start<=1;state<=SWAIT;end
       SWAIT:if(solver_done)begin if(lane==0)begin lane<=1;load_i<=0;state<=RLOAD0;end else state<=SPOST;end
       SPOST:begin
         if(transaction==1)begin copy_i<=0;copy_lane<=0;state<=COPY0;end
         else begin projection_valid<=1;state<=projection_return;end
       end
       TRIAL0:begin trial<=search_center>radius ? search_center-radius:0;
         trial_hi<=search_center+radius<1536 ? search_center+radius:1536;
         best_norm<=0;best_power<=0;state<=TRIAL1;
       end
       TRIAL1:begin
         invalid_trial=0;for(idx=0;idx<MAX_CENTERS;idx=idx+1)if(idx<selected_n)begin tmp_delta=trial-selected[idx];if(tmp_delta<0)tmp_delta=-tmp_delta;if(tmp_delta<=12)invalid_trial=1;end
         if(invalid_trial)state<=TRIALNEXT;else begin trial_count<=trial_count+1'b1;rhs_read(trial,TRIAL2);end
       end
       TRIAL2:begin batch_matrix<=0;batch_row<=0;batch_col<=0;batch_index<=0;batch_length<=selected_n+1'b1;cross_any<=0;state<=GBATCH;end
       DELTA0:dot(DDELTA,{selected_n,1'b0},DELTA1);
       DELTA1:begin rounded=round30_re;delta_re<=rhs_re-rounded[31:0];power_re<=rhs_re-rounded[31:0];
         rounded=round30_im;delta_im<=rhs_im-rounded[31:0];power_im<=rhs_im-rounded[31:0];dot(DPOWER,2,POWER1);end
       POWER0:begin power_re<=delta_re;power_im<=delta_im;dot(DPOWER,2,POWER1);end
       POWER1:begin
         current_power<=dot_result;
         if(current_norm>0 && (best_norm==0 || $unsigned(dot_result)>$unsigned(best_power)))begin
           best_norm<=current_norm;best_power<=dot_result;best_index<=trial;
           best_re<=delta_re;best_im<=delta_im;
         end
         next_trial();
       end
       TRIALNEXT:next_trial();
       BEST0:begin
         if(best_norm<=0)begin projection_valid<=0;state<=projection_return;end
         else begin search_count<=search_count+1'b1;selected_n<=1;transaction<=2;matrix_we<=1;matrix_row<=0;matrix_col<=0;matrix_data<=best_norm;state<=FSTART;end
       end
       NEW0:begin if(!projection_valid)begin final_i<=0;state<=FINAL0;end
         else begin ram_addr<=WSOL;work_return<=NEW1;state<=WORKREAD;end
       end
       NEW1:begin if($unsigned(dot_result)<EARLY_POWER)begin final_i<=0;state<=FINAL0;end
         else if(probe_pending)begin
           // Diagnostic only: no new member, no higher-order joint refit.
           probe_grid<=best_index+256;probe_power<=dot_result;probe_report<=1;
           final_i<=0;state<=FINAL0;
         end else if(count<MAX_CENTERS)begin indices[count]<=best_index;count<=count+1'b1;joint_purpose<=0;state<=JOINT0;end
         else begin error_code<=8'h44;state<=FAIL;end
       end
       JOINT0:begin for(idx=0;idx<MAX_CENTERS;idx=idx+1)if(idx<count)selected[idx]<=indices[idx];selected_n<=count;transaction<=1;load_i<=0;state<=LOADG;end
       JOINT1:begin
         if(joint_purpose==2)begin coordinate<=coordinate+1'b1;state<=COORD0;end
         else if(joint_purpose==0)begin sweep<=0;coordinate<=0;state<=COORD0;end
         else if(sweep<3)begin sweep<=sweep+1'b1;coordinate<=0;state<=COORD0;end
         else if(count<MAX_CENTERS)state<=PEAK0;
         else if(MAX_CENTERS<5 && !probe_pending)begin capacity_reached<=1;probe_pending<=1;state<=PEAK0;end
         else begin capacity_reached<=1;final_i<=0;state<=FINAL0;end
       end
       COORD0:begin coordinate_mode<=1;search_center<=indices[coordinate];radius<=8;projection_return<=COORD1;state<=PREP;end
       COORD1:begin if(projection_valid)indices[coordinate]<=best_index;
         joint_purpose<=coordinate+1<count ? 3'd2:3'd1;state<=JOINT0;
       end
       FINAL0:begin if(final_i>=count)begin emit_i<=0;state<=EMIT;end
         else begin ram_addr<=WCOEFF+({6'b0,final_i}<<1);work_return<=FINAL1;state<=WORKREAD;end
       end
       FINAL1:begin final_current_power<=dot_result;work_we<=1;work_waddr<=WPOWER+({6'b0,final_i}<<1);work_wdata<=dot_result[63:0];tmp_sum=indices[final_i]+256;
         tmp_base=tmp_sum>>4;if((tmp_sum & 15)>8 || ((tmp_sum & 15)==8 && (tmp_base & 1)))tmp_base=tmp_base+1;
         final_peak<=tmp_base;side_i<=0;state<=SIDE0;
       end
       SIDE0:begin
         if(side_i==0)begin work_we<=1;work_waddr<=WPOWER+({6'b0,final_i}<<1)+1'b1;work_wdata<=64'd0;end
         if(side_i<9)residual_read(final_peak-12+side_i,SIDE1);else residual_read(final_peak+4+(side_i-9),SIDE1);
       end
       SIDE1:begin power_re<=residual_re;power_im<=residual_im;dot(DPOWER,2,SIDE2);end
       SIDE2:begin sqrt_work<=dot_result[63:0];sqrt_rem<=0;sqrt_root<=0;sqrt_left<=32;state<=SQRT0;end
       SQRT0:begin sqrt_work<={sqrt_work[61:0],2'b0};sqrt_rem<=sqrt_take ? sqrt_difference[33:0]:sqrt_trial;
         sqrt_root<={sqrt_root[30:0],sqrt_take};sqrt_left<=sqrt_left-1'b1;if(sqrt_left==1)state<=SQRT1;
       end
       SQRT1:begin sort_value<=sqrt_root;sort_j<=side_i;state<=SORTSET;end
       SORTSET:begin
         if(sort_j==0)begin work_we<=1;work_waddr<=WMAG;work_wdata<={32'b0,sort_value};state<=SORT1;end
         else begin ram_addr<=WMAG+sort_j-1'b1;state<=SORTWAIT;end
       end
       SORTWAIT:state<=SORT0;
       SORT0:begin
         work_we<=1;work_waddr<=WMAG+sort_j;
         if(raw_q[31:0]>sort_value)begin work_wdata<={32'b0,raw_q[31:0]};sort_j<=sort_j-1'b1;state<=SORTSET;end
         else begin work_wdata<={32'b0,sort_value};state<=SORT1;end
       end
       SORT1:begin if(side_i<17)begin side_i<=side_i+1'b1;state<=SIDE0;end else state<=FINAL2;end
       FINAL2:begin ram_addr<=WMAG+9'd8;work_return<=MEDIAN;state<=WORKREAD;end
       FINAL3:begin power_re<=median_q30;power_im<=0;dot(DPOWER,1,JOINT1+8'd100);end
       8'd141:begin if(final_current_power>=OUTER_POWER && final_current_power>=($unsigned(dot_result)<<3)+($unsigned(dot_result)<<1))final_mask[final_i]<=1;
         final_i<=final_i+1'b1;state<=FINAL0;
       end
       EMIT:begin
         tmp_n=MAX_CENTERS;tmp_sum=2048;
         for(idx=0;idx<MAX_CENTERS;idx=idx+1)if(idx<count && final_mask[idx] && !emitted_mask[idx] && indices[idx]<tmp_sum)begin tmp_n=idx;tmp_sum=indices[idx];end
         if(tmp_n<MAX_CENTERS)begin emit_i<=tmp_n;ram_addr<=WPOWER+({6'b0,tmp_n}<<1);work_return<=EMIT;state<=WORKREAD;end
         else if(probe_report)begin out_valid<=1;overflow_candidate<=1;grid_abs<=probe_grid;amplitude_square<={4'b0,probe_power,12'b0};probe_report<=0;state<=FINISH;end
         else state<=FINISH;
       end
       FINISH:begin busy<=0;done<=1;last_cycles<=cycles+1'b1;state<=IDLE;end
       FAIL:begin busy<=0;done<=1;error<=1;last_cycles<=cycles+1'b1;state<=IDLE;end
       RHS0:state<=RHS1;
       RHS1:begin if(cache_map_q!=9'd511)begin cache_data_addr<=cache_map_q;state<=RHSHIT0;end
         else if(cache_allocate>=511)begin error_code<=8'h43;state<=FAIL;end
         else begin rhs_tap<=0;rhs_tag<=0;rhs_lane<=0;acc_re<=0;acc_im<=0;state<=RHSSET;rhs_count<=rhs_count+1'b1;end
       end
       RHSHIT0:state<=RHSHIT1;
       RHSHIT1:begin rhs_re<=$signed({{4{cache_q[27]}},cache_q[27:0]})<<<2;rhs_im<=$signed({{4{cache_q[55]}},cache_q[55:28]})<<<2;state<=return_rhs;end
       RHSSET:begin template_addr<=template_address(rhs_index[3:0],rhs_tap);ram_addr<=16+(rhs_index>>4)-6+rhs_tap;state<=RHSWAIT;end
       RHSWAIT:state<=RHSISSUE;
       RHSISSUE:if(mul_ready)begin rhs_tag<=rhs_tag+1'b1;
         if(!rhs_lane)begin rhs_lane<=1;
           if(rhs_tap<12)begin template_addr<=template_address(rhs_index[3:0],rhs_tap+1'b1);ram_addr<=16+(rhs_index>>4)-6+rhs_tap+1'b1;end
         end
         else begin rhs_lane<=0;if(rhs_tap==12)state<=RHSDRAIN;else rhs_tap<=rhs_tap+1'b1;end
       end
       RHSDRAIN:;
       RES0:state<=RES1;
       RES1:begin if(rv_q==res_generation)begin residual_re<=res_q[31:0];residual_im<=res_q[63:32];state<=return_res;end
         else begin res_sum_re<=0;res_sum_im<=0;res_member<=0;state<=RES2;end
       end
       RES2:begin if(res_member>=count)state<=RESEND;
         else begin tmp_base=16+(indices[res_member]>>4)-6;tmp_offset=res_bin-tmp_base;
           if(tmp_offset>=0 && tmp_offset<13)begin template_addr<=template_address(indices[res_member][3:0],tmp_offset);state<=REST0;end
           else res_member<=res_member+1'b1;
         end
       end
       REST0:state<=REST1;
       REST1:dot(DRES,2,RESADD);
       RESADD:begin res_sum_re<=res_sum_re+dot_result;res_sum_im<=res_sum_im+dot_im_result;res_member<=res_member+1'b1;state<=RES2;end
       RESEND:begin
         ram_addr<=res_bin;state<=REST2;
       end
       REST2:begin state<=8'd142;end
       8'd142:begin
         rounded=$signed(raw_q[31:0])-round25_re;
         if(rounded!==$signed(rounded[31:0]))begin error_code<=8'h42;state<=FAIL;end
         else begin residual_re<=rounded[31:0];res_wdata[31:0]<=rounded[31:0];rounded=$signed(raw_q[63:32])-round25_im;
           if(rounded!==$signed(rounded[31:0]))begin error_code<=8'h42;state<=FAIL;end
           else begin residual_im<=rounded[31:0];res_wdata[63:32]<=rounded[31:0];res_waddr<=res_bin;res_we<=1;rv_we<=1;rv_wdata<=res_generation;state<=return_res;end
         end
       end
       DOT:if(mul_ready && (!ram_mul_issue || !work_we))begin if(dot_index+1<dot_length)dot_index<=dot_index+1'b1;else state<=DRAIN;end
       DRAIN:;
       COPY0:begin ram_addr<=WSOL+({6'b0,copy_i}<<1)+copy_lane;state<=COPY1;end
       COPY1:state<=COPY2;
       COPY2:begin
         work_we<=1;work_waddr<=WCOEFF+({6'b0,copy_i}<<1)+copy_lane;work_wdata<=raw_q;
         if(!copy_lane)begin copy_lane<=1;state<=COPY0;end
         else if(copy_i+1<count)begin copy_i<=copy_i+1'b1;copy_lane<=0;state<=COPY0;end
         else begin refit_count<=refit_count+1'b1;res_generation<=res_generation+1'b1;state<=JOINT1;end
       end
       WORKREAD:state<=WORKWAIT;
       WORKWAIT:begin
         if(work_return==EMIT)begin out_valid<=1;grid_abs<=indices[emit_i]+256;amplitude_square<={4'b0,raw_q,12'b0};emitted_mask[emit_i]<=1;state<=EMIT;end
         else if(work_return==MEDIAN)begin median_q30<=rne($signed({32'd0,raw_q[31:0]})+$signed({32'd0,work_q[31:0]}),6);state<=FINAL3;end
         else begin power_re<=raw_q[31:0];power_im<=work_q[31:0];dot(DPOWER,2,work_return);end
       end
       GBATCH:begin
         gram_addr<=batch_lookup[10:0];batch_negative0<=batch_lookup[11];batch_zero0<=batch_lookup[12];
         batch_index0<=batch_index;batch_row0<=batch_row;batch_col0<=batch_col;
         if(batch_index+1>=batch_length)state<=GDRAIN;
         else begin batch_index<=batch_index+1'b1;
           if(batch_matrix)begin if(batch_col+1<selected_n)batch_col<=batch_col+1'b1;else begin batch_col<=0;batch_row<=batch_row+1'b1;end end
         end
       end
       GDRAIN:;
       default:begin error_code<=8'h7f;state<=FAIL;end
     endcase
     if(batch_valid1)begin
       if(batch_zero1)batch_value=0;
       else if(batch_negative1)batch_value=-rne({{32{gram_q[31]}},gram_q},2);
       else batch_value=rne({{32{gram_q[31]}},gram_q},2);
       if(batch_matrix)begin matrix_we<=1;matrix_row<=batch_row1;matrix_col<=batch_col1;matrix_data<=batch_value;
         if(batch_index1+1==batch_length)state<=FSTART;
       end else begin
         if(batch_index1==0)current_norm<=batch_value;
         else begin work_we<=1;work_waddr<=WCROSS+(batch_index1-1'b1);work_wdata<={32'b0,batch_value};if(batch_value!=0)cross_any<=1;end
         if(batch_index1+1==batch_length)begin
           if(selected_n==0 || (!cross_any && batch_value==0))begin delta_re<=rhs_re;delta_im<=rhs_im;state<=POWER0;end
           else state<=DELTA0;
         end
       end
     end
     if(mul_valid && !mul_tag[7] && (state==DOT || state==DRAIN))begin
       if(dot_mode==DDELTA || dot_mode==DRES)begin
         if(mul_tag[0])acc_im<=acc_im+mul_product;else acc_re<=acc_re+mul_product;
       end else acc_re<=acc_re+mul_product;
       if(mul_tag+1==dot_length)begin
         if(dot_mode==DDELTA || dot_mode==DRES)begin dot_result<=acc_re;dot_im_result<=acc_im+mul_product;end
         else dot_result<=acc_re+mul_product;
         state<=return_dot;
       end
     end
     if(mul_valid && (state==RHSSET || state==RHSWAIT || state==RHSISSUE || state==RHSDRAIN))begin
       if(mul_tag[0])acc_im<=acc_im+mul_product;else acc_re<=acc_re+mul_product;
       if(mul_tag==25)begin
         rounded=round25_re;cache_wdata[27:0]<=rounded[27:0];rhs_re<=rounded[31:0]<<<2;
         if(rounded!==$signed(rounded[27:0]))begin error_code<=8'h41;state<=FAIL;end
         else begin rounded=round25_im;cache_wdata[55:28]<=rounded[27:0];rhs_im<=rounded[31:0]<<<2;
           if(rounded!==$signed(rounded[27:0]))begin error_code<=8'h41;state<=FAIL;end
           else begin cache_waddr<=rhs_index;cache_data_waddr<=cache_allocate[8:0];cache_map_wdata<=cache_allocate[8:0];
             cache_allocate<=cache_allocate+1'b1;cache_we<=1;cv_we<=1;state<=return_rhs;end
         end
       end
     end
     if(solver_error && busy)begin error_code<={4'h3,solver_error_code};state<=FAIL;end
     if(fft_valid && busy)begin error_code<=8'h40;state<=FAIL;end
   end
 end
endmodule

// 512x64 raw bank; explicit normal-mode GW5A DPBs. No reset or added latency.
module raw_dpb64_q24(input clk,input[8:0] addr_a,addr_b,
 input write_a,write_b,read_a,read_b,input[63:0] data_a,data_b,
 output[63:0] q_a,q_b);
 wire[13:0] physical_a={1'b0,addr_a,2'b00,2'b11};
 wire[13:0] physical_b={1'b0,addr_b,2'b00,2'b11};
 // Final C4 report: work_wdata[45] -> RAM DIB[13], hold -0.038ns.
 // Two kept inversions provide combinational data delay only; no cycle,
 // sign, precision, read/write enable, reset or address semantics change.
 (* syn_keep=1 *) wire hold45_inverse;
 (* syn_keep=1 *) wire hold45_value;
 (* syn_keep=1,syn_preserve=1 *) LUT1 #(.INIT(2'b01)) hold45_a(.I0(data_b[45]),.F(hold45_inverse));
 (* syn_keep=1,syn_preserve=1 *) LUT1 #(.INIT(2'b01)) hold45_b(.I0(hold45_inverse),.F(hold45_value));
 wire[63:0] timed_data_b={data_b[63:46],hold45_value,data_b[44:0]};
 genvar raw_lane;
 generate for(raw_lane=0;raw_lane<4;raw_lane=raw_lane+1)begin:raw_dpb_lane
   DPB #(.READ_MODE0(1'b0),.READ_MODE1(1'b0),
     .WRITE_MODE0(2'b00),.WRITE_MODE1(2'b00),
     .BIT_WIDTH_0(16),.BIT_WIDTH_1(16),
     .BLK_SEL_0(3'b000),.BLK_SEL_1(3'b000),.RESET_MODE("SYNC")) ram(
       .DOA(q_a[16*raw_lane+:16]),.DOB(q_b[16*raw_lane+:16]),
       .DIA(data_a[16*raw_lane+:16]),.DIB(timed_data_b[16*raw_lane+:16]),
       .BLKSELA(3'b000),.BLKSELB(3'b000),.ADA(physical_a),.ADB(physical_b),
       .WREA(write_a),.WREB(write_b),.CLKA(clk),.CLKB(clk),
       .CEA(write_a||read_a),.CEB(write_b||read_b),
       .OCEA(1'b1),.OCEB(1'b1),.RESETA(1'b0),.RESETB(1'b0));
 end endgenerate
endmodule
