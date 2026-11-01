`timescale 1ns/1ps
// Full, coupled 1..5 real LDL in signed32 Q24. Matrix must be completely
// reloaded for each factorization; valid L/D is reused for fresh RHS vectors.
// A/L are synchronous block RAMs. One II=1 two-clock signed32x32 multiplier
// is shared with external scalar/dot arithmetic while internal work is idle.
module fitting_solver #(parameter integer MAX_CENTERS=5)(
 input clk,input rst_n,
 input matrix_we,input [2:0] matrix_row,input [2:0] matrix_col,input signed [31:0] matrix_data,
 input rhs_we,input [2:0] rhs_index,input signed [31:0] rhs_data,
 input factor_start,input solve_start,input [2:0] n,
 output reg busy,output reg factor_valid,output reg done,output reg error,output reg [3:0] error_code,
 output reg [31:0] cycles,output reg [31:0] rejected_starts,
 output reg result_valid,output reg [2:0] result_index,output reg signed [31:0] result_data,
 input meta_read,input meta_inverse,input [2:0] meta_row,input [2:0] meta_col,
 output meta_ready,output reg meta_valid,output reg signed [31:0] meta_data,
 input ext_mul_valid,input signed [31:0] ext_mul_a,input signed [31:0] ext_mul_b,input [7:0] ext_mul_tag,
 output ext_mul_ready,output ext_mul_out_valid,output signed [63:0] ext_mul_product,output [7:0] ext_mul_out_tag
);
 // Capacity affects storage/addresses only; arithmetic remains signed32 Q24.
 // Two vector banks reuse the strict upper triangle. For capacity3/4 its
 // 3/6 entries cannot hold all6/8 vector cells, so only the missing3/2
 // cells occupy a tail after the Z bank. No L/D cell is overwritten.
 localparam integer MATRIX_WORDS=MAX_CENTERS*MAX_CENTERS;
 localparam integer UPPER_WORDS=MAX_CENTERS*(MAX_CENTERS-1)/2;
 localparam integer VECTOR_TAIL=(2*MAX_CENTERS>UPPER_WORDS) ? 2*MAX_CENTERS-UPPER_WORDS:0;
 localparam integer STORE_WORDS=(MAX_CENTERS==5) ? 32:MATRIX_WORDS+MAX_CENTERS+VECTOR_TAIL;
 localparam integer ADDR_BITS=(STORE_WORDS>16) ? 5:4;
 // synthesis translate_off
 initial begin
   if(MAX_CENTERS<3 || MAX_CENTERS>5)$fatal(1,"MAX_CENTERS must be3,4,5");
 end
 // synthesis translate_on
 localparam [31:0] ONE=32'd16777216;
 localparam [5:0] IDLE=0,DRAIN=1,CHECK=2,SYM_SET=4,SYM_WAIT=5,SYM_FIRST=6,SYM_WAIT2=7,SYM_SECOND=8,
   PIV_SET=9,PIV_WAIT=10,PIV_USE=11,DIV_LOOP=12,DIV_DONE=13,
   COL_SET=14,COL_WAIT=15,COL_MUL=16,COL_RESULT=17,
   UP_SET=18,UP_WAIT=19,UP_MUL=20,UP_RESULT=21,UP_WAIT_A=22,UP_STORE=23,UP_MIRROR=24,UP_NEXT=25,
   FACT_DONE=26,FWD_SET=27,FWD_WAIT=28,FWD_MUL=29,FWD_RESULT=30,
   DIAG_MUL=31,DIAG_RESULT=32,BACK_SET=33,BACK_WAIT=34,BACK_MUL=35,BACK_RESULT=36,EMIT=37,FAILED=39,
   FWD_RWAIT=40,FWD_RUSE=41,DIAG_SET=42,DIAG_WAIT=43,BACK_ZWAIT=44,BACK_ZUSE=45,EMIT_WAIT=46,EMIT_STREAM=47,DIV_STORE=48;
 reg [5:0] state;
 reg command_factor;reg [2:0] n_work,factor_n,i,j,k,emit_index;
 reg [3:0] failure_code;
 reg [MATRIX_WORDS-1:0] matrix_valid,matrix_loaded;
 reg [MAX_CENTERS-1:0] rhs_valid,rhs_loaded;
 // Keep all40 bits: low36 in one BSRAM and high4 in registers.
 // Each logical bank has STORE_WORDS entries:15,22,32 for capacity3,4,5.
 (* syn_ramstyle="block_ram" *) reg [31:0] a_low[0:STORE_WORDS-1];
 (* syn_ramstyle="block_ram" *) reg [31:0] l_low[0:STORE_WORDS-1];
 // After full symmetry checking, the strict upper triangle is reused
 // for Y/X and inverse/RHS. Extra vector cells lie after Z; Z starts at
 // MATRIX_WORDS. Lower-triangle/diagonal factor cells stay disjoint.
 reg signed [31:0] back_hold;reg back_first;
 reg [ADDR_BITS-1:0] a_raddr,l_raddr,a_waddr,l_waddr;
 reg a_we,l_we;reg signed [31:0] a_wdata,l_wdata,a_q,l_q,symmetric_first,acc,update_product;
 reg [31:0] div_den,div_rem;
 reg [48:0] div_quotient;reg [5:0] div_bit;
 wire [32:0] div_trial={div_rem,(div_bit==6'd48)};
 wire [33:0] div_difference={1'b0,div_trial}-{2'b0,div_den};
 wire div_take=!div_difference[33];
 wire [32:0] div_remainder=div_take ? div_difference[32:0]:div_trial;
 wire [32:0] div_trial_second={div_remainder[31:0],1'b0};
 wire [33:0] div_difference_second={1'b0,div_trial_second}-{2'b0,div_den};
 wire div_take_second=!div_difference_second[33];
 wire [32:0] div_remainder_second=div_take_second ? div_difference_second[32:0]:div_trial_second;
 wire [32:0] div_double_rem={div_rem,1'b0};
 wire div_round=div_double_rem>{1'b0,div_den} || (div_double_rem=={1'b0,div_den} && div_quotient[0]);
 wire [49:0] div_rounded={1'b0,div_quotient}+div_round;
 function [5:0] pivot_highest;
 input [31:0] value;integer bit_index;
 begin pivot_highest=0;for(bit_index=0;bit_index<31;bit_index=bit_index+1)if(value[bit_index])pivot_highest=bit_index[5:0];end
 endfunction
 wire [5:0] pivot_high=pivot_highest(a_q);
 function [ADDR_BITS-1:0] address;
 input [2:0] row;input [2:0] col;
 begin address=row*MAX_CENTERS+col;end
 endfunction
 function [ADDR_BITS-1:0] vector_slot;
 input [3:0] rank;integer row,col,slot;
 begin
   vector_slot=0;slot=0;
   for(row=0;row<MAX_CENTERS;row=row+1)begin
     for(col=0;col<MAX_CENTERS;col=col+1)begin
       if(row<col)begin
         if(rank==slot)vector_slot=row*MAX_CENTERS+col;
         slot=slot+1;
       end
     end
   end
   if(rank>=UPPER_WORDS && rank<2*MAX_CENTERS)
     vector_slot=MATRIX_WORDS+MAX_CENTERS+rank-UPPER_WORDS;
 end
 endfunction
 function [ADDR_BITS-1:0] vector_first;
 input [2:0] index;
 begin
   if(MAX_CENTERS==5)begin case(index)0:vector_first=1;1:vector_first=2;2:vector_first=3;3:vector_first=4;default:vector_first=7;endcase end
   else vector_first=vector_slot({1'b0,index});
 end
 endfunction
 function [ADDR_BITS-1:0] vector_second;
 input [2:0] index;
 begin
   if(MAX_CENTERS==5)begin case(index)0:vector_second=8;1:vector_second=9;2:vector_second=13;3:vector_second=14;default:vector_second=19;endcase end
   else vector_second=vector_slot(MAX_CENTERS+{1'b0,index});
 end
 endfunction
 function [MATRIX_WORDS-1:0] needed_matrix;
 input [2:0] count;integer row,col;
 begin
   needed_matrix=0;
   for(row=0;row<MAX_CENTERS;row=row+1)for(col=0;col<MAX_CENTERS;col=col+1)
     if(row<count && col<count)needed_matrix[row*MAX_CENTERS+col]=1'b1;
 end
 endfunction
 function [MAX_CENTERS-1:0] needed_rhs;
 input [2:0] count;integer index;
 begin needed_rhs=0;for(index=0;index<MAX_CENTERS;index=index+1)if(index<count)needed_rhs[index]=1'b1;end
 endfunction
 function signed [63:0] rne24_full;
 input signed [63:0] value;reg signed [40:0] rounded;
 begin
   rounded={value[63],value[63:24]};
   if(value[23:0]>24'h800000 || (value[23:0]==24'h800000 && rounded[0]))rounded=rounded+1'b1;
   rne24_full={{23{rounded[40]}},rounded};
 end
 endfunction
 wire [ADDR_BITS-1:0] external_matrix_addr=address(matrix_row,matrix_col);
 wire external_matrix_we=matrix_we && !busy && matrix_row<MAX_CENTERS && matrix_col<MAX_CENTERS;
 wire external_rhs_we=rhs_we && !busy && rhs_index<MAX_CENTERS;
 wire [ADDR_BITS-1:0] external_rhs_addr=vector_second(rhs_index);
 assign meta_ready=!busy && factor_valid && !matrix_we && !factor_start && !solve_start && rst_n &&
                   meta_row<factor_n && (meta_inverse || (meta_col<=meta_row && meta_col<factor_n));
 wire meta_accept=meta_read && meta_ready;
 wire [ADDR_BITS-1:0] effective_l_raddr=meta_accept ? (meta_inverse ? vector_first(meta_row):address(meta_row,meta_col)):l_raddr;
 reg meta_pending;
 always @(posedge clk)begin
   a_q<=a_low[a_raddr];
   l_q<=l_low[effective_l_raddr];
   if(rst_n)begin
     if(external_matrix_we)begin a_low[external_matrix_addr]<=matrix_data[31:0];end
     else if(a_we)begin a_low[a_waddr]<=a_wdata[31:0];end
     if(external_rhs_we)begin l_low[external_rhs_addr]<=rhs_data[31:0];end
     else if(l_we)begin l_low[l_waddr]<=l_wdata[31:0];end
   end
 end
 always @(posedge clk)begin
   if(!rst_n)begin meta_pending<=0;meta_valid<=0;meta_data<=0;end
   else begin
     meta_pending<=meta_accept;
     meta_valid<=meta_pending;
     if(meta_pending)meta_data<=l_q;
   end
 end

 // Physical multiplication appears once; external work cannot interleave with
 // an internal factor/solve. Already accepted external requests drain first.
 reg signed [31:0] internal_a,internal_b;reg internal_valid;
 always @* begin
   internal_valid=1;internal_a=0;internal_b=0;
   case(state)
     COL_MUL:begin internal_a=a_q;internal_b=l_q;end
     UP_MUL:begin internal_a=l_q;internal_b=a_q;end
     FWD_MUL:begin internal_a=l_q;internal_b=a_q;end
     DIAG_MUL:begin internal_a=a_q;internal_b=l_q;end
     BACK_MUL:begin internal_a=l_q;internal_b=back_first ? back_hold:a_q;end
     default:internal_valid=0;
   endcase
 end
 assign ext_mul_ready=!busy && !factor_start && !solve_start && rst_n;
 wire external_accept=ext_mul_valid && ext_mul_ready;
 reg signed [31:0] mul_a0,mul_b0;reg [7:0] mul_tag0,mul_tag1;
 reg mul_valid0,mul_valid1,mul_external0,mul_external1;
 reg signed [63:0] mul_product;
 always @(posedge clk)begin
   if(!rst_n)begin
     mul_valid0<=0;mul_valid1<=0;mul_external0<=0;mul_external1<=0;
     mul_a0<=0;mul_b0<=0;mul_tag0<=0;mul_tag1<=0;mul_product<=0;
   end else begin
     mul_valid0<=internal_valid || external_accept;
     mul_external0<=external_accept;
     mul_a0<=external_accept ? ext_mul_a:internal_a;
     mul_b0<=external_accept ? ext_mul_b:internal_b;
     mul_tag0<=ext_mul_tag;
     mul_product<=mul_a0*mul_b0;
     mul_valid1<=mul_valid0;mul_external1<=mul_external0;mul_tag1<=mul_tag0;
   end
 end
 assign ext_mul_out_valid=mul_valid1 && mul_external1;
 assign ext_mul_product=mul_product;
 assign ext_mul_out_tag=mul_tag1;
 wire internal_result=mul_valid1 && !mul_external1;
 wire signed [63:0] rounded_product=rne24_full(mul_product);
 wire product_overflow=rounded_product>64'sd2147483647 || rounded_product < -64'sd2147483648;
 wire signed [31:0] product_q24=rounded_product[31:0];
 wire signed [32:0] update_difference={a_q[31],a_q}-{update_product[31],update_product};
 wire signed [32:0] solve_difference={acc[31],acc}-{product_q24[31],product_q24};

 always @(posedge clk)begin
   done<=0;result_valid<=0;a_we<=0;l_we<=0;
   if(!rst_n)begin
     busy<=0;factor_valid<=0;error<=0;error_code<=0;cycles<=0;rejected_starts<=0;
     result_index<=0;result_data<=0;state<=IDLE;command_factor<=0;n_work<=0;factor_n<=0;
     i<=0;j<=0;k<=0;emit_index<=0;failure_code<=0;
     matrix_valid<=0;matrix_loaded<=0;rhs_valid<=0;rhs_loaded<=0;
     a_raddr<=0;l_raddr<=0;a_waddr<=0;l_waddr<=0;a_wdata<=0;l_wdata<=0;
     symmetric_first<=0;acc<=0;update_product<=0;div_den<=0;div_rem<=0;div_quotient<=0;div_bit<=0;back_hold<=0;back_first<=0;
   end else begin
     if(busy)cycles<=cycles+1'b1;
     if((factor_start || solve_start) && busy)rejected_starts<=rejected_starts+1'b1;
     if(matrix_we && !busy)begin
       factor_valid<=0;
       if(matrix_row<MAX_CENTERS && matrix_col<MAX_CENTERS)matrix_valid[external_matrix_addr]<=1'b1;
       else begin error<=1;error_code<=4'd1;end
     end
     if(rhs_we && !busy && rhs_index<MAX_CENTERS)rhs_valid[rhs_index]<=1'b1;
     case(state)
       IDLE:if(factor_start || solve_start)begin
         busy<=1;cycles<=1;error<=0;error_code<=0;n_work<=n;command_factor<=factor_start;
         matrix_loaded<=matrix_valid;rhs_loaded<=rhs_valid;
         if(factor_start)begin factor_valid<=0;matrix_valid<=0;end else rhs_valid<=0;
         if((factor_start && solve_start) || matrix_we || rhs_we)begin failure_code<=7;state<=FAILED;end
         else state<=DRAIN;
       end
       DRAIN:if(!mul_valid0 && !mul_valid1)state<=CHECK;
       CHECK:begin
         if(n_work==0 || n_work>MAX_CENTERS)begin failure_code<=1;state<=FAILED;end
         else if(command_factor)begin
           if((matrix_loaded & needed_matrix(n_work))!=needed_matrix(n_work))begin failure_code<=6;state<=FAILED;end
           else if(n_work==1)begin j<=0;state<=PIV_SET;end
           else begin i<=1;j<=0;state<=SYM_SET;end
         end else if(!factor_valid || n_work!=factor_n)begin failure_code<=8;state<=FAILED;end
         else if((rhs_loaded & needed_rhs(n_work))!=needed_rhs(n_work))begin failure_code<=6;state<=FAILED;end
         else begin i<=0;k<=0;state<=FWD_SET;end
       end
       SYM_SET:begin a_raddr<=address(i,j);state<=SYM_WAIT;end
       SYM_WAIT:state<=SYM_FIRST;
       SYM_FIRST:begin symmetric_first<=a_q;a_raddr<=address(j,i);state<=SYM_WAIT2;end
       SYM_WAIT2:state<=SYM_SECOND;
       SYM_SECOND:begin
         if(a_q!=symmetric_first)begin failure_code<=5;state<=FAILED;end
         else if(j+1'b1<i)begin j<=j+1'b1;state<=SYM_SET;end
         else if(i+1'b1<n_work)begin i<=i+1'b1;j<=0;state<=SYM_SET;end
         else begin j<=0;state<=PIV_SET;end
       end
       PIV_SET:begin a_raddr<=address(j,j);state<=PIV_WAIT;end
       PIV_WAIT:state<=PIV_USE;
       PIV_USE:begin
         if(a_q<=0)begin failure_code<=2;state<=FAILED;end
         else begin
           // Skip only mathematically-known leading zero quotient bits of
           // 2^60/pivot, then execute two exact restoring steps per clock.
           div_den<=a_q;div_rem<=pivot_high==0 ? 32'd0:(32'd1<<(pivot_high-1'b1));
           div_quotient<=0;div_bit<=6'd48-pivot_high;state<=DIV_LOOP;
         end
       end
       DIV_LOOP:begin
         if(div_bit==0)begin
           div_rem<=div_remainder[31:0];div_quotient<={div_quotient[47:0],div_take};state<=DIV_DONE;
         end else begin
           div_rem<=div_remainder_second[31:0];div_quotient<={div_quotient[46:0],div_take,div_take_second};
           if(div_bit==1)state<=DIV_DONE;else div_bit<=div_bit-2'd2;
         end
       end
       DIV_DONE:begin
         if(div_rounded==0 || div_rounded>50'd2147483647)begin failure_code<=4;state<=FAILED;end
         else begin
           l_we<=1;l_waddr<=vector_first(j);l_wdata<=div_rounded[31:0];state<=DIV_STORE;
         end
       end
       DIV_STORE:begin l_we<=1;l_waddr<=address(j,j);l_wdata<=ONE;
         if(j+1'b1<n_work)begin i<=j+1'b1;state<=COL_SET;end else state<=FACT_DONE;
       end
       COL_SET:begin a_raddr<=address(i,j);l_raddr<=vector_first(j);state<=COL_WAIT;end
       COL_WAIT:state<=COL_MUL;
       COL_MUL:state<=COL_RESULT;
       COL_RESULT:if(internal_result)begin
         if(product_overflow)begin failure_code<=3;state<=FAILED;end
         else begin
           l_we<=1;l_waddr<=address(i,j);l_wdata<=product_q24;
           if(i+1'b1<n_work)begin i<=i+1'b1;state<=COL_SET;end
           else begin i<=j+1'b1;k<=j+1'b1;state<=UP_SET;end
         end
       end
       UP_SET:begin l_raddr<=address(i,j);a_raddr<=address(k,j);state<=UP_WAIT;end
       UP_WAIT:state<=UP_MUL;
       UP_MUL:state<=UP_RESULT;
       UP_RESULT:if(internal_result)begin
         if(product_overflow)begin failure_code<=3;state<=FAILED;end
         else begin update_product<=product_q24;a_raddr<=address(i,k);state<=UP_WAIT_A;end
       end
       UP_WAIT_A:state<=UP_STORE;
       UP_STORE:begin
         if(update_difference[32]!=update_difference[31])begin failure_code<=3;state<=FAILED;end
         else begin a_we<=1;a_waddr<=address(i,k);a_wdata<=update_difference[31:0];state<=UP_MIRROR;end
       end
       UP_MIRROR:begin
         if(i!=k)begin a_we<=1;a_waddr<=address(k,i);a_wdata<=a_wdata;end
         state<=UP_NEXT;
       end
       UP_NEXT:begin
         if(k+1'b1<=i)begin k<=k+1'b1;state<=UP_SET;end
         else if(i+1'b1<n_work)begin i<=i+1'b1;k<=j+1'b1;state<=UP_SET;end
         else begin j<=j+1'b1;state<=PIV_SET;end
       end
       FACT_DONE:begin factor_valid<=1;factor_n<=n_work;done<=1;busy<=0;state<=IDLE;end
       FWD_SET:begin l_raddr<=vector_second(i);state<=FWD_RWAIT;end
       FWD_RWAIT:state<=FWD_RUSE;
       FWD_RUSE:begin
         acc<=l_q;k<=0;
         if(i==0)begin
           a_we<=1;a_waddr<=vector_first(0);a_wdata<=l_q;
           if(n_work==1)begin i<=0;state<=DIAG_SET;end
           else begin i<=1;l_raddr<=vector_second(1);state<=FWD_RWAIT;end
         end else begin l_raddr<=address(i,0);a_raddr<=vector_first(0);state<=FWD_WAIT;end
       end
       FWD_WAIT:state<=FWD_MUL;
       FWD_MUL:begin
         // Once the final product has latched its L value, the same read
         // port can fetch the next row's RHS while that product completes.
         if(k+1'b1==i && i+1'b1<n_work)l_raddr<=vector_second(i+1'b1);
         state<=FWD_RESULT;
       end
       FWD_RESULT:if(internal_result)begin
         if(product_overflow || solve_difference[32]!=solve_difference[31])begin failure_code<=3;state<=FAILED;end
         else if(k+1'b1<i)begin acc<=solve_difference[31:0];k<=k+1'b1;l_raddr<=address(i,k+1'b1);a_raddr<=vector_first(k+1'b1);state<=FWD_WAIT;end
         else begin
           a_we<=1;a_waddr<=vector_first(i);a_wdata<=solve_difference[31:0];
           if(i+1'b1<n_work)begin acc<=l_q;i<=i+1'b1;k<=0;l_raddr<=address(i+1'b1,0);a_raddr<=vector_first(0);state<=FWD_WAIT;end
           else begin i<=0;state<=DIAG_SET;end
         end
       end
       DIAG_SET:begin a_raddr<=vector_first(i);l_raddr<=vector_first(i);state<=DIAG_WAIT;end
       DIAG_WAIT:state<=DIAG_MUL;
       DIAG_MUL:begin
         if(i+1'b1<n_work)begin a_raddr<=vector_first(i+1'b1);l_raddr<=vector_first(i+1'b1);end
         state<=DIAG_RESULT;
       end
       DIAG_RESULT:if(internal_result)begin
         if(product_overflow)begin failure_code<=3;state<=FAILED;end
         else begin
           a_we<=1;a_waddr<=MATRIX_WORDS+i;a_wdata<=product_q24;
           if(i+1'b1<n_work)begin i<=i+1'b1;state<=DIAG_MUL;end
           else begin i<=n_work-1'b1;state<=BACK_SET;end
         end
       end
       BACK_SET:begin a_raddr<=MATRIX_WORDS+i;state<=BACK_ZWAIT;end
       BACK_ZWAIT:state<=BACK_ZUSE;
       BACK_ZUSE:begin
         acc<=a_q;k<=i+1'b1;
         if(i+1'b1==n_work)begin
           a_we<=1;a_waddr<=vector_second(i);a_wdata<=a_q;back_hold<=a_q;back_first<=1;
           if(i==0)begin emit_index<=0;state<=EMIT;end else begin i<=i-1'b1;state<=BACK_SET;end
         end else begin l_raddr<=address(i+1'b1,i);a_raddr<=vector_second(i+1'b1);back_first<=1;state<=BACK_WAIT;end
       end
       BACK_WAIT:state<=BACK_MUL;
       BACK_MUL:begin
         if(k+1'b1==n_work && i>0)a_raddr<=MATRIX_WORDS+i-1'b1;
         state<=BACK_RESULT;
       end
       BACK_RESULT:if(internal_result)begin
         if(product_overflow || solve_difference[32]!=solve_difference[31])begin failure_code<=3;state<=FAILED;end
         else if(k+1'b1<n_work)begin acc<=solve_difference[31:0];k<=k+1'b1;l_raddr<=address(k+1'b1,i);a_raddr<=vector_second(k+1'b1);back_first<=0;state<=BACK_WAIT;end
         else begin
           a_we<=1;a_waddr<=vector_second(i);a_wdata<=solve_difference[31:0];back_hold<=solve_difference[31:0];
           if(i==0)begin emit_index<=0;state<=EMIT;end
           else begin acc<=a_q;k<=i;l_raddr<=address(i,i-1'b1);a_raddr<=vector_second(i);i<=i-1'b1;back_first<=1;state<=BACK_WAIT;end
         end
       end
       EMIT:begin a_raddr<=vector_second(0);state<=EMIT_WAIT;end
       EMIT_WAIT:begin a_raddr<=vector_second(1);state<=EMIT_STREAM;end
       EMIT_STREAM:begin
         result_valid<=1;result_index<=emit_index;result_data<=a_q;
         if(emit_index+1'b1==n_work)begin done<=1;busy<=0;state<=IDLE;end
         else begin emit_index<=emit_index+1'b1;if(emit_index+2<n_work)a_raddr<=vector_second(emit_index+2'd2);end
       end
       FAILED:begin
         error<=1;error_code<=failure_code;done<=1;busy<=0;state<=IDLE;
         if(command_factor)factor_valid<=0;
       end
       default:begin busy<=0;state<=IDLE;factor_valid<=0;error<=1;error_code<=7;done<=1;end
     endcase
   end
 end
endmodule
