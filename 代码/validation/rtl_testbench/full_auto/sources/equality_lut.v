`timescale 1ns/1ps
// Two independent bit equalities per LUT4. Explicit primitive mapping avoids
// the synthesis carry-chain equality mapping; all comparator registers and
// scheduler clocks/priority/authorization remain exactly as before.
module v27_equal_lut #(parameter WIDTH=32)(input[WIDTH-1:0] a,b,output equal);
 localparam PAIRS=(WIDTH+1)/2,GROUPS=(PAIRS+3)/4;
 wire[PAIRS-1:0] pair_equal;wire[GROUPS-1:0] group_equal;
 genvar p,g,k;
 generate for(p=0;p<PAIRS;p=p+1)begin:pair
   if(2*p+1<WIDTH)begin:two
     LUT4 #(.INIT(16'h9009)) unit(.F(pair_equal[p]),.I0(a[2*p]),.I1(b[2*p]),.I2(a[2*p+1]),.I3(b[2*p+1]));
   end else begin:one
     LUT4 #(.INIT(16'h9009)) unit(.F(pair_equal[p]),.I0(a[2*p]),.I1(b[2*p]),.I2(1'b0),.I3(1'b0));
   end
 end
 for(g=0;g<GROUPS;g=g+1)begin:group_reduce
   wire[3:0] bits;
   for(k=0;k<4;k=k+1)begin:pad
     if(4*g+k<PAIRS)assign bits[k]=pair_equal[4*g+k];else assign bits[k]=1'b1;
   end
   LUT4 #(.INIT(16'h8000)) unit(.F(group_equal[g]),.I0(bits[0]),.I1(bits[1]),.I2(bits[2]),.I3(bits[3]));
 end
 endgenerate
 wire[3:0] final_bits;
 generate for(k=0;k<4;k=k+1)begin:final_pad
   if(k<GROUPS)assign final_bits[k]=group_equal[k];else assign final_bits[k]=1'b1;
 end endgenerate
 LUT4 #(.INIT(16'h8000)) final_cell(.F(equal),.I0(final_bits[0]),.I1(final_bits[1]),.I2(final_bits[2]),.I3(final_bits[3]));
endmodule
