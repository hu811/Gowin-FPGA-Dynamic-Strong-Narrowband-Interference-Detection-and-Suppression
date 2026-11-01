`timescale 1ns/1ps
// Read-only display. A frame freezes all fields before I2C transfer.
// time_cycles: latest window-end to observation-complete computation clocks,
// NOT acquisition time, FIR group delay, or suppression response time.
module oled_dashboard #(parameter MAX_CENTERS=4,CLK_HZ=50000000,I2C_HZ=100000,
 WAIT_CYCLES=10000000,REFRESH_CYCLES=5000000)
(input clk,input rst_n,input[3*MAX_CENTERS-1:0] groups,
 input[MAX_CENTERS-1:0] active,input[1:0] waiting,input running,input fading,
 input observed,input[16:0] time_cycles,input time_overflow,
 input fault,input blocked,input ambiguous,
 inout oled_scl,inout oled_sda,output reg link_ok);
 localparam INIT=0, SNAP=1, CONVERT=2, LOW=3, HIGH=4, PAGE=5, DATA=6, ON=7;
 reg[2:0] stage,page;
 reg[6:0] column;
 reg[4:0] init_index;
 reg[23:0] delay_count;
 reg address_select,request,awaiting;
 reg[1:0] settle;
 reg[1:0] single_count,pair_count;
 reg[2:0] screen_state;
 reg[16:0] remaining;
 reg[11:0] time_bcd;
 reg time_valid;
 reg[2:0] population[0:2];
 reg[1:0] singles,pairs;
 integer i,j;
 always @*begin
   singles=0;pairs=0;
   for(j=0;j<3;j=j+1)begin
     population[j]=0;
     for(i=0;i<MAX_CENTERS;i=i+1)population[j]=population[j]+groups[j*MAX_CENTERS+i];
     if(population[j]==1)singles=singles+1'b1;
     if(population[j]==2)pairs=pairs+1'b1;
   end
 end
 // A single 2Kx8 ROM contains the background, numeric glyphs, state words,
 // and the board-verified initialization sequence. No framebuffer or DSP.
 reg[7:0] screen_rom[0:2047] /* synthesis syn_romstyle="block_rom" */;
 initial $readmemh("rom/oled_screen.mem",screen_rom);
 reg[10:0] rom_address;
 reg[7:0] rom_q;
 reg[3:0] digit;
 reg dynamic_digit;
 wire[3:0] char_index=column[6:3];
 always @*begin
   rom_address={1'b0,page,column};digit=0;dynamic_digit=0;
   if(stage==INIT)rom_address={6'b101100,init_index};
   else if(stage==DATA)begin
     if(page==0 && char_index==14)begin dynamic_digit=1;digit=MAX_CENTERS;end
     if(page==2 && char_index==7)begin dynamic_digit=1;digit={2'd0,single_count};end
     if(page==2 && char_index==15)begin dynamic_digit=1;digit={2'd0,pair_count};end
     if(page==4 && time_valid)case(char_index)
       6:begin dynamic_digit=1;digit=time_bcd[11:8];end
       8:begin dynamic_digit=1;digit=time_bcd[7:4];end
       9:begin dynamic_digit=1;digit=time_bcd[3:0];end
     endcase
     if(dynamic_digit)rom_address=11'd1024+{4'd0,digit,column[2:0]};
     if(page==6 && char_index>=6 && char_index<14)
       rom_address={2'b11,screen_state,(column[5:4]+2'd1),column[3:0]};
   end
 end
 always @(posedge clk)rom_q<=screen_rom[rom_address];
 reg[7:0] payload;
 always @*case(stage)
   INIT,DATA:payload=rom_q;
   LOW:payload=0;
   HIGH:payload=8'h10;
   PAGE:payload={5'b10110,page};
   ON:payload=8'haf;
   default:payload=0;
 endcase
 wire busy,done,error;
 i2c_write3 #(.CLK_HZ(CLK_HZ),.I2C_HZ(I2C_HZ)) bus(clk,rst_n,request,
   address_select?7'h3d:7'h3c,stage==DATA?8'h40:8'h00,payload,
   oled_scl,oled_sda,busy,done,error);
 always @(posedge clk)begin
   request<=0;
   if(!rst_n)begin
     stage<=INIT;page<=0;column<=0;init_index<=0;delay_count<=WAIT_CYCLES;
     address_select<=0;request<=0;awaiting<=0;settle<=0;link_ok<=0;
     single_count<=0;pair_count<=0;screen_state<=0;
     remaining<=0;time_bcd<=0;time_valid<=0;
   end else begin
     if(delay_count!=0)delay_count<=delay_count-1'b1;
     else if(stage==SNAP)begin
       single_count<=singles;pair_count<=pairs;
       // Priority: error > capacity/ambiguity > preparing > fading > filter > bypass > wait.
       screen_state<=fault?7:blocked?6:ambiguous?5:(running||waiting!=0)?2:fading?3:active!=0?4:observed?1:0;
       remaining<=time_cycles;time_bcd<=0;time_valid<=observed&&!time_overflow;
       stage<=CONVERT;
     end else if(stage==CONVERT)begin
       // 500 cycles at 50MHz =0.01ms. Floor, exact integer conversion.
       if(remaining>=500)begin
         remaining<=remaining-17'd500;
         if(time_bcd[3:0]==9)begin
           time_bcd[3:0]<=0;
           if(time_bcd[7:4]==9)begin time_bcd[7:4]<=0;time_bcd[11:8]<=time_bcd[11:8]+1'b1;end
           else time_bcd[7:4]<=time_bcd[7:4]+1'b1;
         end else time_bcd[3:0]<=time_bcd[3:0]+1'b1;
       end else begin stage<=LOW;page<=0;settle<=0;end
     end else if(!awaiting&&!busy)begin
       if(settle!=2)settle<=settle+1'b1;
       else begin request<=1;awaiting<=1;end
     end
     if(done)begin
       awaiting<=0;settle<=0;
       if(error)begin stage<=INIT;init_index<=0;page<=0;column<=0;link_ok<=0;
         address_select<=!address_select;delay_count<=WAIT_CYCLES;end
       else case(stage)
         INIT:if(init_index==24)stage<=SNAP;else init_index<=init_index+1'b1;
         LOW:stage<=HIGH;
         HIGH:stage<=PAGE;
         PAGE:begin column<=0;stage<=DATA;end
         DATA:if(column==127)begin if(page==7)stage<=ON;else begin page<=page+1'b1;stage<=LOW;end end
              else column<=column+1'b1;
         ON:begin link_ok<=1;stage<=SNAP;delay_count<=REFRESH_CYCLES;end
         default:stage<=INIT;
       endcase
     end
   end
 end
endmodule
