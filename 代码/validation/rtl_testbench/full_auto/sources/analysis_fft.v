`timescale 1ns/1ps
// Loaded-frame, shared radix-2 512/1024 FFT. All RAM reads are synchronous.
// One registered signed 32x32 multiplier is reused for windowing, four-product
// complex rotation, and output power. Q29 FFT stages each round nearest-even /2.
// Final complex output is 4/N DFT(x*periodic_hann), Q29; power is Q58.
module analysis_fft #(
 parameter HANN_FILE="rom/fft_hann_half_q30.mem",
 parameter TWIDDLE_RE_FILE="rom/fft_twiddle_re_q30.mem",
 parameter TWIDDLE_IM_FILE="rom/fft_twiddle_im_q30.mem"
)(
 input clk,input rst_n,
 input load_we,input [9:0] load_addr,input signed [15:0] load_sample,
 input start,input mode_1024,input [31:0] frame_seq,
 output reg busy,output reg done,
 output reg out_valid,output reg [9:0] out_bin,
 output reg signed [31:0] out_real,output reg signed [31:0] out_imag,
 output reg [63:0] out_power,output reg [31:0] out_frame_seq,
 output reg [31:0] frame_cycles,output reg [31:0] rejected_starts
);
 reg signed [15:0] samples[0:1023] /* synthesis syn_ramstyle="block_ram" */;
 // FFT work memory uses supported normal-mode DPBs below.
 // The frozen1024-word Hann table is bitwise symmetric. Store0..511;
 // index512 is exactly2^30, and513..1023 mirror511..1.
 (* syn_romstyle="block_rom" *) reg signed [31:0] hann[0:511];
 (* syn_romstyle="block_rom" *) reg signed [31:0] twiddle_real[0:511];
 (* syn_romstyle="block_rom" *) reg signed [31:0] twiddle_imag[0:511];
 initial begin
   $readmemb(HANN_FILE,hann);
   $readmemb(TWIDDLE_RE_FILE,twiddle_real);
   $readmemb(TWIDDLE_IM_FILE,twiddle_imag);
 end
 reg [4:0] state;
 reg large_frame;reg [3:0] last_stage,stage;
 reg [9:0] init_index,group_base,offset,half_span,output_index;
 reg [31:0] seq_hold,cycles;
 reg signed [15:0] sample_q;
 reg signed [31:0] hann_raw_q,twiddle_real_q,twiddle_imag_q;
 reg hann_mid_q;
 wire signed [31:0] hann_q=hann_mid_q ? 32'sd1073741824:hann_raw_q;
 wire signed [31:0] ram_a_real,ram_a_imag,ram_b_real,ram_b_imag;
 reg signed [31:0] a_real,a_imag,b_real,b_imag,tw_real,tw_imag;
 reg signed [31:0] t_real,t_imag,mul_a,mul_b,output_real_hold,output_imag_hold;
 reg signed [63:0] product,p1,p3;
 reg signed [64:0] rotation_real;
 reg [63:0] square_real;
 wire [9:0] hann_address=large_frame ? init_index : {init_index[8:0],1'b0};
 wire [9:0] hann_mirrored=hann_address[9] ? 10'd0-hann_address:hann_address;
 wire [9:0] twiddle_address_full=offset << (4'd10-stage);
 wire [8:0] twiddle_address=twiddle_address_full[8:0];
 wire [9:0] butterfly_a=group_base+offset;
 wire [9:0] butterfly_b=butterfly_a+half_span;
 wire [10:0] next_group={1'b0,group_base}+({1'b0,half_span}<<1);
 wire [10:0] n_samples=large_frame ? 11'd1024 : 11'd512;
 wire [9:0] last_bin=large_frame ? 10'd512 : 10'd256;
 wire [9:0] bit_reverse=large_frame ?
   {init_index[0],init_index[1],init_index[2],init_index[3],init_index[4],init_index[5],init_index[6],init_index[7],init_index[8],init_index[9]}:
   {1'b0,init_index[0],init_index[1],init_index[2],init_index[3],init_index[4],init_index[5],init_index[6],init_index[7],init_index[8]};

 function signed [31:0] rne16;
 input signed [63:0] v;reg signed [63:0] q;
 begin q=v>>>16;if(v[15:0]>16'h8000 || (v[15:0]==16'h8000 && q[0]))q=q+1'b1;rne16=q[31:0];end
 endfunction
 function signed [31:0] rne30;
 input signed [64:0] v;reg signed [64:0] q;
 begin q=v>>>30;if(v[29:0]>30'h20000000 || (v[29:0]==30'h20000000 && q[0]))q=q+1'b1;rne30=q[31:0];end
 endfunction
 function signed [31:0] rne1;
 input signed [32:0] v;reg signed [32:0] q;
 begin q=v>>>1;if(v[0] && q[0])q=q+1'b1;rne1=q[31:0];end
 endfunction
 function signed [31:0] amplitude_scale;
 input signed [31:0] v;reg signed [33:0] scaled;
 begin
   scaled={{2{v[31]}},v}<<<2;
   if(scaled>34'sd2147483647)amplitude_scale=32'sh7fffffff;
   else if(scaled < -34'sd2147483648)amplitude_scale=32'sh80000000;
   else amplitude_scale=scaled[31:0];
 end
 endfunction

 // True dual-port inference: each physical port has a single synchronous
 // read and optional write. The two writes target different butterfly bins.
 wire [9:0] ram_a_addr=(state<=4) ? bit_reverse : (state>=14 ? output_index : butterfly_a);
 wire [9:0] ram_b_addr=butterfly_b;
 wire ram_a_we=rst_n && (state==4 || state==13);
 wire ram_b_we=rst_n && state==13;
 wire signed [32:0] ar_plus={a_real[31],a_real}+{t_real[31],t_real};
 wire signed [32:0] ai_plus={a_imag[31],a_imag}+{t_imag[31],t_imag};
 wire signed [32:0] ar_minus={a_real[31],a_real}-{t_real[31],t_real};
 wire signed [32:0] ai_minus={a_imag[31],a_imag}-{t_imag[31],t_imag};
 wire signed [31:0] ram_a_wreal=state==4 ? rne16(product) : rne1(ar_plus);
 wire signed [31:0] ram_a_wimag=state==4 ? 32'sd0 : rne1(ai_plus);
 always @(posedge clk)begin
   if(load_we && !busy && rst_n)samples[load_addr]<=load_sample;
   sample_q<=samples[init_index];hann_raw_q<=hann[hann_mirrored[8:0]];
   hann_mid_q<=hann_address==10'd512;
   twiddle_real_q<=twiddle_real[twiddle_address];twiddle_imag_q<=twiddle_imag[twiddle_address];
   product<=mul_a*mul_b;
 end
 // A write-edge read result is unused: state4 is followed by state1/6,
 // state13 by state6/14. Consumers7/15 always follow a fresh nonwrite read.
 // Disable cross-port collision reads too; physical read/write alias is not
 // assigned a defined value by the GW5A primitive.
 wire same_ram_address=ram_a_addr==ram_b_addr;
 wire ram_a_re=!ram_a_we && !(ram_b_we && same_ram_address);
 wire ram_b_re=!ram_b_we && !(ram_a_we && same_ram_address);
 fft_work_dpb64 work_bank(.clk(clk),.addr_a(ram_a_addr),.addr_b(ram_b_addr),
   .write_a(ram_a_we),.write_b(ram_b_we),.read_a(ram_a_re),.read_b(ram_b_re),
   .data_a({ram_a_wimag,ram_a_wreal}),.data_b({rne1(ai_minus),rne1(ar_minus)}),
   .q_a({ram_a_imag,ram_a_real}),.q_b({ram_b_imag,ram_b_real}));
 // synthesis translate_off
 reg last_a_invalid,last_b_invalid;
 always @(posedge clk)begin
   if(rst_n)begin
     if(ram_a_we && ram_b_we && same_ram_address)$fatal(1,"FFT simultaneous writes alias");
     if((state==7 || state==15) && last_a_invalid)$fatal(1,"FFT consumes collided A read");
     if(state==7 && last_b_invalid)$fatal(1,"FFT consumes collided B read");
   end
   last_a_invalid<=!ram_a_re;last_b_invalid<=!ram_b_re;
 end
 // synthesis translate_on


 always @(posedge clk)begin
   out_valid<=0;done<=0;
   if(!rst_n)begin
     busy<=0;state<=0;large_frame<=0;last_stage<=9;stage<=1;
     init_index<=0;group_base<=0;offset<=0;half_span<=1;output_index<=0;
     seq_hold<=0;cycles<=0;frame_cycles<=0;rejected_starts<=0;
     mul_a<=0;mul_b<=0;a_real<=0;a_imag<=0;b_real<=0;b_imag<=0;tw_real<=0;tw_imag<=0;
     t_real<=0;t_imag<=0;p1<=0;p3<=0;rotation_real<=0;square_real<=0;
     output_real_hold<=0;output_imag_hold<=0;out_real<=0;out_imag<=0;
     out_power<=0;out_bin<=0;out_frame_seq<=0;
   end else begin
     if(busy)cycles<=cycles+1'b1;
     if(start && busy)rejected_starts<=rejected_starts+1'b1;
     case(state)
       0:if(start)begin
         busy<=1;cycles<=1;large_frame<=mode_1024;last_stage<=mode_1024 ? 4'd10 : 4'd9;
         seq_hold<=frame_seq;init_index<=0;stage<=1;group_base<=0;offset<=0;half_span<=1;state<=1;
       end
       1:state<=2; // sample / Hann synchronous read
       2:begin mul_a<={{16{sample_q[15]}},sample_q};mul_b<=hann_q;state<=3;end
       3:state<=4; // registered multiplier
       4:begin // windowed write, bit-reversed
         if({1'b0,init_index}+1'b1==n_samples)state<=6;
         else begin init_index<=init_index+1'b1;state<=1;end
       end
       6:state<=7; // two synchronous FFT reads and twiddle reads
       7:begin
         a_real<=ram_a_real;a_imag<=ram_a_imag;b_real<=ram_b_real;b_imag<=ram_b_imag;
         tw_real<=twiddle_real_q;tw_imag<=twiddle_imag_q;
         mul_a<=ram_b_real;mul_b<=twiddle_real_q;state<=8;
       end
       8:begin mul_a<=b_imag;mul_b<=tw_imag;state<=9;end
       9:begin p1<=product;mul_a<=b_imag;mul_b<=tw_real;state<=10;end
       10:begin rotation_real<={p1[63],p1}-{product[63],product};mul_a<=b_real;mul_b<=tw_imag;state<=11;end
       11:begin p3<=product;state<=12;end
       12:begin t_real<=rne30(rotation_real);t_imag<=rne30({p3[63],p3}+{product[63],product});state<=13;end
       13:begin // simultaneous butterfly writes
         if(offset+1'b1<half_span)begin offset<=offset+1'b1;state<=6;end
         else if(next_group<n_samples)begin offset<=0;group_base<=next_group[9:0];state<=6;end
         else if(stage<last_stage)begin stage<=stage+1'b1;half_span<=half_span<<1;offset<=0;group_base<=0;state<=6;end
         else begin output_index<=0;state<=14;end
       end
       14:state<=15; // output RAM read
       15:begin
         output_real_hold<=amplitude_scale(ram_a_real);output_imag_hold<=amplitude_scale(ram_a_imag);
         mul_a<=amplitude_scale(ram_a_real);mul_b<=amplitude_scale(ram_a_real);state<=16;
       end
       16:begin mul_a<=output_imag_hold;mul_b<=output_imag_hold;state<=17;end
       17:begin square_real<=product;state<=18;end
       18:begin
         out_valid<=1;out_bin<=output_index;out_real<=output_real_hold;out_imag<=output_imag_hold;
         out_power<=square_real+product;out_frame_seq<=seq_hold;
         if(output_index==last_bin)begin busy<=0;done<=1;frame_cycles<=cycles;state<=0;end
         else begin output_index<=output_index+1'b1;state<=14;end
       end
       default:begin state<=0;busy<=0;end
     endcase
   end
 end
endmodule

// Four16-bit lanes,1024 logical words. Width16 address[13:4] selects the
// word and[1:0]=11 enables both bytes. READ_MODE=0 is one synchronous edge.
module fft_work_dpb64(input clk,input[9:0] addr_a,addr_b,
 input write_a,write_b,read_a,read_b,input[63:0] data_a,data_b,
 output[63:0] q_a,q_b);
 wire[13:0] physical_a={addr_a,2'b00,2'b11};
 wire[13:0] physical_b={addr_b,2'b00,2'b11};
 genvar lane;
 generate for(lane=0;lane<4;lane=lane+1)begin:fft_lane
   DPB #(.READ_MODE0(1'b0),.READ_MODE1(1'b0),
     .WRITE_MODE0(2'b00),.WRITE_MODE1(2'b00),.BIT_WIDTH_0(16),.BIT_WIDTH_1(16),
     .BLK_SEL_0(3'b000),.BLK_SEL_1(3'b000),.RESET_MODE("SYNC")) ram(
     .DOA(q_a[16*lane+:16]),.DOB(q_b[16*lane+:16]),
     .DIA(data_a[16*lane+:16]),.DIB(data_b[16*lane+:16]),
     .BLKSELA(3'b000),.BLKSELB(3'b000),.ADA(physical_a),.ADB(physical_b),
     .WREA(write_a),.WREB(write_b),.CLKA(clk),.CLKB(clk),
     .CEA(write_a || read_a),.CEB(write_b || read_b),
     .OCEA(1'b1),.OCEB(1'b1),.RESETA(1'b0),.RESETB(1'b0));
 end endgenerate
endmodule
