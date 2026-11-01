`timescale 1ns/1ps
// Build ONE symmetric correction (321 stored taps); not a frequency-indexed FIR LUT.
// p is exact archived Q16 prototype, c is interpolated quarter-wave Q16 cosine.
// D=sum_full(2*p*c*c) Q48. b_q16=RNE((98*p*c)<<32 / (50*D)).
module coefficient_generator #(parameter GRID_ALIGNED=0)(input clk,input rst_n,input start,input[31:0] frequency_word,
 output reg busy,output reg done,output reg error,
 output reg coef_valid,output reg[8:0] coef_address,output reg signed[17:0] coef_value,
 output reg[31:0] last_cycles,output[63:0] normalization);




 reg[4:0] state;reg[8:0] tap;reg[31:0] phase,word;
 // Grid FSM: at most 321*(12+14)+1 = 8347 reported clocks.
 // Divider has ten fixed steps, no external ready/stall input.
 // Rejected requests finish earlier; retain 32-bit external counter outputs.
 localparam integer CYCLE_BITS=GRID_ALIGNED ? 14:32;
 reg[CYCLE_BITS-1:0] cycles;
 // synthesis translate_off
 always @(posedge clk)if(rst_n && GRID_ALIGNED && busy && cycles>8348)
   $fatal(1,"coefficient generator cycle bound violated");
 // synthesis translate_on
 reg[30:0] angle;reg[11:0] cos_addr;wire[16:0] cos_q;reg[16:0] c0,c1;wire signed[17:0] p_q;reg signed[17:0] c;
 reg endpoint,endpoint_next,negative;reg[17:0] fraction;
 reg[34:0] interpolation_product,interpolation;
 reg signed[24:0] pc;wire signed[24:0] pc_q;
 reg[39:0] pc_square;
 reg[49:0] gain;reg[54:0] denominator;reg[62:0] numerator;reg div_start,coef_negative;
 wire div_busy,div_done;wire[79:0] quotient;
 // For FCW multiples of 2^19, interpolation fraction is exactly zero.
 // Select the sampled cosine explicitly: avoids Gowin Education constant
 // propagation of the zero-fraction multiply corrupting this datapath.
 // States/latency, coefficient format, normalization and rounding unchanged.
 wire[16:0] grid_magnitude;
 wire[17:0] cos_magnitude=GRID_ALIGNED ? {1'b0,grid_magnitude} : {1'b0,interpolation[34:18]}+
      ((interpolation[17:0]>18'h20000) || (interpolation[17:0]==18'h20000 && interpolation[18]));
 wire[24:0] absolute_pc=pc_q[24] ? -pc_q:pc_q;
 wire[62:0] extended_pc={38'd0,absolute_pc};
 // Exact constant arithmetic in otherwise available DSP blocks.
 // The original state registers and 55/63-bit truncation points are retained.
 wire[47:0] pc_times98,den_low,den_high;
 generator_abs_scale scale_pc(pc_q,pc_times98);
 generator_const_mult #(.K(50)) scale_gain_low({3'd0,gain[23:0]},48'd0,den_low);
 generator_const_mult #(.K(50)) scale_gain_high({1'b0,gain[49:24]},
                                               {24'd0,den_low[47:24]},den_high);
 wire[62:0] scaled_pc={15'd0,pc_times98};
 wire[54:0] den_product={den_high[30:0],den_low[23:0]};
 assign normalization={14'd0,gain};
 wire[79:0] numerator_full={17'd0,numerator};
 wire[63:0] denominator_full={9'd0,denominator};
 divide_coefficient_bound10 divider(clk,rst_n,div_start,numerator_full,denominator_full,div_busy,div_done,quotient);

 // Exact direct table for the existing even-index Q16 cosine grid.
 // Two 2048x8 physical ROM blocks trade the remaining RAM block for
 // substantially fewer decoder registers/LUTs. Indices 0..5 round to 65536.
 // angle is fixed after state 1; synchronous data is ready well before
 // the unchanged state-9 consume point. No additional cycles or rounding.
 generate if(GRID_ALIGNED)begin:cos_grid
   reg[15:0] rom[0:2047] /* synthesis syn_romstyle="block_rom" */;
   initial $readmemb("rom/cos_grid_direct.mem",rom);
   reg[15:0] q;
   always @(posedge clk)q<=rom[angle[29:19]];
   assign grid_magnitude=angle[30] ? 17'd0:
                         angle[29:19]<=5 ? 17'd65536:{1'b0,q};
   assign cos_q=17'd0;
 end else begin:cos_generic
   reg[16:0] rom[0:4095] /* synthesis syn_romstyle="block_rom" */;
   initial $readmemb("rom/cos_quarter.mem",rom);
   reg[16:0] q;
   always @(posedge clk)q<=rom[cos_addr];
   assign cos_q=q;assign grid_magnitude=17'd0;
 end endgenerate
 generate if(GRID_ALIGNED)begin:packed_work
   wire[31:0] q;
   generator_work_store store(clk,state==11,tap,pc,q);
   assign p_q=$signed({10'd0,q[31:25]})+18'sd53;
   assign pc_q=q[24:0];
 end else begin:generic_work
   reg signed[17:0] p_rom[0:511] /* synthesis syn_romstyle="block_rom" */;
   reg signed[24:0] pc_memory[0:511] /* synthesis syn_ramstyle="block_ram" */;
   reg signed[17:0] pq;reg signed[24:0] pcq;
   initial $readmemb("rom/prototype.mem",p_rom);
   always @(posedge clk)begin
     pq<=p_rom[tap];
     if(state==11)pc_memory[tap]<=pc;
     if(state==14)pcq<=pc_memory[tap];
   end
   assign p_q=pq;assign pc_q=pcq;
 end endgenerate
 always @(posedge clk)begin
   done<=0;coef_valid<=0;div_start<=0;
   if(!rst_n)begin
     state<=0;busy<=0;done<=0;error<=0;coef_valid<=0;coef_address<=0;coef_value<=0;
     tap<=0;phase<=0;word<=0;cycles<=0;last_cycles<=0;angle<=0;cos_addr<=0;
     c0<=0;c1<=0;endpoint<=0;endpoint_next<=0;negative<=0;fraction<=0;
     interpolation_product<=0;interpolation<=0;pc<=0;pc_square<=0;gain<=0;
     c<=0;denominator<=1;numerator<=0;div_start<=0;coef_negative<=0;
   end else begin
     if(busy)cycles<=cycles+1'b1;
     case(state)
       0:if(start)begin
         error<=0;cycles<=1;
         if(frequency_word==0 || frequency_word>=32'h80000000)begin done<=1;error<=1;end
         else begin
           word<=frequency_word;phase<=(frequency_word<<8)+(frequency_word<<6);
           busy<=1;tap<=0;gain<=0;state<=1;
         end
       end
       1:begin
         angle<=phase[30] ? 31'h40000000-{1'b0,phase[29:0]}:{1'b0,phase[29:0]};
         negative<=phase[31]^phase[30];state<=2;
       end
       2:begin
         cos_addr<=angle[29:18];fraction<=angle[17:0];endpoint<=angle[30];
         endpoint_next<=angle[30] || angle[29:18]==4095;state<=3;
       end
       3:state<=4;
       4:begin c0<=endpoint ? 17'd0:cos_q;cos_addr<=cos_addr+1'b1;state<=5;end
       5:state<=6;
       6:begin c1<=endpoint_next ? 17'd0:cos_q;state<=7;end
       7:begin interpolation_product<=(c0-c1)*fraction;state<=8;end
       8:begin interpolation<={c0,18'd0}-interpolation_product;state<=9;end
       9:begin c<=negative ? -$signed(cos_magnitude):$signed(cos_magnitude);state<=10;end
       10:begin pc<=p_q*c;state<=11;end
       11:begin pc_square<=pc*c;state<=12;end
       12:begin
         gain<=gain+({10'd0,pc_square} << (tap==320 ? 1:2));
         if(tap==320)state<=13;else begin tap<=tap+1'b1;phase<=phase-word;state<=1;end
       end
       13:begin
         if(gain<64'h0000800000000000)begin busy<=0;error<=1;done<=1;state<=0;end
         else begin denominator<=den_product;tap<=0;state<=14;end
       end
       14:state<=15;
       15:begin numerator<=scaled_pc<<32;coef_negative<=pc_q[24];div_start<=1;state<=16;end
       16:state<=17;
       17:if(div_done)begin
         if(quotient>80'd131071)begin error<=1;busy<=0;done<=1;state<=0;end
         else begin
           coef_valid<=1;coef_address<=tap;coef_value<=coef_negative ? -$signed(quotient[17:0]):$signed(quotient[17:0]);
           if(tap==320)begin busy<=0;done<=1;last_cycles<=cycles;state<=0;end
           else begin tap<=tap+1'b1;state<=14;end
         end
       end
       default:state<=0;
     endcase
   end
 end
endmodule

// One physical single-port 512x32 RAM. Upper bits retain exact prototype.
// State 11 writes pc; state 10 already read the same tap's prototype.
// State 14 reads pc for state 15; read/write phases never overlap.
module generator_work_store(input clk,input we,input[8:0] address,
 input[24:0] pc,output[31:0] q);
 SP #(.READ_MODE(1'b0),.WRITE_MODE(2'b00),.BIT_WIDTH(32),
 .BLK_SEL(3'b000),.RESET_MODE("SYNC"),
 .INIT_RAM_00(256'h0400000004000000020000000200000002000000000000000000000000000000),
 .INIT_RAM_01(256'h0A0000000A000000080000000800000008000000060000000600000004000000),
 .INIT_RAM_02(256'h10000000100000000E0000000E0000000C0000000C0000000C0000000A000000),
 .INIT_RAM_03(256'h1600000014000000140000001400000012000000120000001200000010000000),
 .INIT_RAM_04(256'h1C0000001A0000001A0000001A00000018000000180000001800000016000000),
 .INIT_RAM_05(256'h220000002000000020000000200000001E0000001E0000001C0000001C000000),
 .INIT_RAM_06(256'h2800000026000000260000002600000024000000240000002200000022000000),
 .INIT_RAM_07(256'h2C0000002C0000002C0000002A0000002A0000002A0000002800000028000000),
 .INIT_RAM_08(256'h3200000032000000320000003000000030000000300000002E0000002E000000),
 .INIT_RAM_09(256'h3800000038000000380000003600000036000000340000003400000034000000),
 .INIT_RAM_0A(256'h3E0000003E0000003C0000003C0000003C0000003A0000003A0000003A000000),
 .INIT_RAM_0B(256'h440000004200000042000000420000004000000040000000400000003E000000),
 .INIT_RAM_0C(256'h4A00000048000000480000004800000046000000460000004600000044000000),
 .INIT_RAM_0D(256'h4E0000004E0000004E0000004C0000004C0000004C0000004A0000004A000000),
 .INIT_RAM_0E(256'h5400000054000000520000005200000052000000500000005000000050000000),
 .INIT_RAM_0F(256'h5A00000058000000580000005800000056000000560000005600000054000000),
 .INIT_RAM_10(256'h5E0000005E0000005C0000005C0000005C0000005A0000005A0000005A000000),
 .INIT_RAM_11(256'h640000006200000062000000620000006000000060000000600000005E000000),
 .INIT_RAM_12(256'h6800000068000000660000006600000066000000640000006400000064000000),
 .INIT_RAM_13(256'h6C0000006C0000006C0000006A0000006A0000006A0000006A00000068000000),
 .INIT_RAM_14(256'h700000007000000070000000700000006E0000006E0000006E0000006E000000),
 .INIT_RAM_15(256'h7600000074000000740000007400000074000000720000007200000072000000),
 .INIT_RAM_16(256'h7A00000078000000780000007800000078000000760000007600000076000000),
 .INIT_RAM_17(256'h7E0000007C0000007C0000007C0000007C0000007A0000007A0000007A000000),
 .INIT_RAM_18(256'h82000000800000008000000080000000800000007E0000007E0000007E000000),
 .INIT_RAM_19(256'h8400000084000000840000008400000082000000820000008200000082000000),
 .INIT_RAM_1A(256'h8800000088000000880000008600000086000000860000008600000086000000),
 .INIT_RAM_1B(256'h8C0000008A0000008A0000008A0000008A0000008A0000008800000088000000),
 .INIT_RAM_1C(256'h8E0000008E0000008E0000008C0000008C0000008C0000008C0000008C000000),
 .INIT_RAM_1D(256'h9000000090000000900000009000000090000000900000008E0000008E000000),
 .INIT_RAM_1E(256'h9400000092000000920000009200000092000000920000009200000092000000),
 .INIT_RAM_1F(256'h9600000096000000940000009400000094000000940000009400000094000000),
 .INIT_RAM_20(256'h9800000098000000960000009600000096000000960000009600000096000000),
 .INIT_RAM_21(256'h9800000098000000980000009800000098000000980000009800000098000000),
 .INIT_RAM_22(256'h9A0000009A0000009A0000009A0000009A0000009A0000009A0000009A000000),
 .INIT_RAM_23(256'h9C0000009C0000009C0000009C0000009C0000009A0000009A0000009A000000),
 .INIT_RAM_24(256'h9C0000009C0000009C0000009C0000009C0000009C0000009C0000009C000000),
 .INIT_RAM_25(256'h9E0000009E0000009E0000009E0000009C0000009C0000009C0000009C000000),
 .INIT_RAM_26(256'h9E0000009E0000009E0000009E0000009E0000009E0000009E0000009E000000),
 .INIT_RAM_27(256'h9E0000009E0000009E0000009E0000009E0000009E0000009E0000009E000000),
 .INIT_RAM_28(256'h000000000000000000000000000000000000000000000000000000009E000000),
 .INIT_RAM_29(256'h0000000000000000000000000000000000000000000000000000000000000000),
 .INIT_RAM_2A(256'h0000000000000000000000000000000000000000000000000000000000000000),
 .INIT_RAM_2B(256'h0000000000000000000000000000000000000000000000000000000000000000),
 .INIT_RAM_2C(256'h0000000000000000000000000000000000000000000000000000000000000000),
 .INIT_RAM_2D(256'h0000000000000000000000000000000000000000000000000000000000000000),
 .INIT_RAM_2E(256'h0000000000000000000000000000000000000000000000000000000000000000),
 .INIT_RAM_2F(256'h0000000000000000000000000000000000000000000000000000000000000000),
 .INIT_RAM_30(256'h0000000000000000000000000000000000000000000000000000000000000000),
 .INIT_RAM_31(256'h0000000000000000000000000000000000000000000000000000000000000000),
 .INIT_RAM_32(256'h0000000000000000000000000000000000000000000000000000000000000000),
 .INIT_RAM_33(256'h0000000000000000000000000000000000000000000000000000000000000000),
 .INIT_RAM_34(256'h0000000000000000000000000000000000000000000000000000000000000000),
 .INIT_RAM_35(256'h0000000000000000000000000000000000000000000000000000000000000000),
 .INIT_RAM_36(256'h0000000000000000000000000000000000000000000000000000000000000000),
 .INIT_RAM_37(256'h0000000000000000000000000000000000000000000000000000000000000000),
 .INIT_RAM_38(256'h0000000000000000000000000000000000000000000000000000000000000000),
 .INIT_RAM_39(256'h0000000000000000000000000000000000000000000000000000000000000000),
 .INIT_RAM_3A(256'h0000000000000000000000000000000000000000000000000000000000000000),
 .INIT_RAM_3B(256'h0000000000000000000000000000000000000000000000000000000000000000),
 .INIT_RAM_3C(256'h0000000000000000000000000000000000000000000000000000000000000000),
 .INIT_RAM_3D(256'h0000000000000000000000000000000000000000000000000000000000000000),
 .INIT_RAM_3E(256'h0000000000000000000000000000000000000000000000000000000000000000),
 .INIT_RAM_3F(256'h0000000000000000000000000000000000000000000000000000000000000000)
 ) memory(.DO(q),.DI({q[31:25],pc}),.BLKSEL(3'b000),
 .AD({address,1'b0,4'b1111}),.WRE(we),.CLK(clk),.CE(1'b1),.OCE(1'b1),.RESET(1'b0));
endmodule

module generator_const_mult #(parameter K=50)(input[26:0] a,input[47:0] c,output[47:0] q);
 // All inputs are nonnegative signed DSP operands; no pipeline registers.
 MULTALU27X18 #(.P_SEL(1'b0),.A_SEL(1'b0),.C_SEL(1'b1),
 .CASI_SEL(1'b0),.ACC_SEL(1'b0),.PRE_LOAD(48'd0)) unit(
 .DOUT(q),.CASO(),.SOA(),.A(a),.B(K[17:0]),.C(c),.D(26'd0),.SIA(27'd0),.CASI(48'd0),
 .PSEL(1'b0),.ASEL(1'b0),.CSEL(1'b1),.CASISEL(1'b0),.PADDSUB(1'b0),
 .ACCSEL(1'b0),.ADDSUB(2'd0),.CLK(2'd0),.CE(2'b11),.RESET(2'd0));
endmodule

module generator_abs_scale(input signed[24:0] pc,output[47:0] q);
 // DSP preadder computes 0+pc or 0-pc, before the exact multiply by 98.
 // 27-bit preadder range safely includes abs(-2^24).
 MULTALU27X18 #(.P_SEL(1'b1),.DYN_P_ADDSUB("TRUE"),.A_SEL(1'b0),.C_SEL(1'b0),
 .CASI_SEL(1'b0),.ACC_SEL(1'b0),.PRE_LOAD(48'd0)) unit(
 .DOUT(q),.CASO(),.SOA(),.A(27'd0),.B(18'd98),.C(48'd0),.D({pc[24],pc}),
 .SIA(27'd0),.CASI(48'd0),.PSEL(1'b1),.ASEL(1'b0),.CSEL(1'b0),
 .CASISEL(1'b0),.PADDSUB(pc[24]),.ACCSEL(1'b0),.ADDSUB(2'd0),
 .CLK(2'd0),.CE(2'b11),.RESET(2'd0));
endmodule
