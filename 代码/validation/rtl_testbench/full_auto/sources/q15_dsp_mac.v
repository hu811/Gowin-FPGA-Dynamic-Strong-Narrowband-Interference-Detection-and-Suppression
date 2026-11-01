`timescale 1ns/1ps
module v27_q15_mac(input[5:0] state,input[8:0] tap,input signed[17:0] a,b,
 input signed[35:0] accumulator,output signed[35:0] next_sum);
 wire merge=state==23;
 wire signed[26:0] aa=(state==4 && tap!=320) ? ($signed({{9{a[17]}},a})<<<1):$signed({{9{a[17]}},a});
 wire[47:0] cc=merge ? $signed({{12{accumulator[35]}},accumulator})<<<15:{{12{accumulator[35]}},accumulator};
 wire signed[47:0] mac;
 MULTALU27X18 #(.P_SEL(1'b0),.A_SEL(1'b0),.C_SEL(1'b1),.CASI_SEL(1'b0),.ACC_SEL(1'b0),.PRE_LOAD(48'd0)) unit(
 .DOUT(mac),.CASO(),.SOA(),.A(aa),.B(b),.C(cc),.D(26'd0),
 .SIA(27'd0),.CASI(48'd0),.PSEL(1'b0),.ASEL(1'b0),.CSEL(1'b1),.CASISEL(1'b0),.PADDSUB(1'b0),.ACCSEL(1'b0),.ADDSUB(2'd0),
 .CLK(2'd0),.CE(2'b11),.RESET(2'd0));
 // Each basis correction<=264 and |weight|<=2*ONE. Four rounded terms give
 // |merge sum|<=2112, so signed13 fully contains every consumed merge value.
 wire[14:0] rem=mac[14:0];
 wire increment=(rem>15'h4000) || (rem==15'h4000 && (mac[15]^accumulator[0]));
 wire signed[12:0] merge_result=$signed(mac[27:15])+increment;
 assign next_sum=merge ? {{23{merge_result[12]}},merge_result}:mac[35:0];
endmodule
module v27_q15_dot_rne(input signed[35:0] value,output signed[35:0] q);
 wire increment=(value[14:0]>15'h4000) || (value[14:0]==15'h4000 && value[15]);
 wire[47:0] rounded;
 MULTALU27X18 #(.P_SEL(1'b0),.A_SEL(1'b0),.C_SEL(1'b1),.CASI_SEL(1'b0),.ACC_SEL(1'b0),.PRE_LOAD(48'd0)) unit(
 .DOUT(rounded),.CASO(),.SOA(),.A({26'd0,increment}),.B(18'd1),.C({30'd0,value[32:15]}),.D(26'd0),
 .SIA(27'd0),.CASI(48'd0),.PSEL(1'b0),.ASEL(1'b0),.CSEL(1'b1),.CASISEL(1'b0),.PADDSUB(1'b0),.ACCSEL(1'b0),.ADDSUB(2'd0),
 .CLK(2'd0),.CE(2'b11),.RESET(2'd0));
 assign q={{18{rounded[17]}},rounded[17:0]};
endmodule
