`timescale 1ns/1ps
// Controlled component experiment, not the production detector/controller.
// Both FIR instances receive identical immutable input windows and complete banks.
// Hard comparator switches to the new bank on the SAME accepted proposal sample.
module tb_switch;
 GSR GSR(.GSRI(1'b1));
 reg clk=0;always #10 clk=~clk;
 reg rst_n=0,iv=0;reg signed[15:0] x=0;reg[31:0] seq=0;
 reg pv=0;reg[1:0] pb=1;reg[31:0] ver=1;
 wire pa,oe,ne,fade;wire[1:0] ob,nb;wire[5:0] alpha;wire[2:0] occupied;
 wire[31:0] commits,av;
 wire fr,hr,fv,hv,fb,hb;wire signed[15:0] fy,hy;wire[31:0] fs,hs,fd,hd,fc,hc;
 wire[15:0] fm,hm;
 reg we=0;reg[1:0] wb=1;reg[8:0] wa=0;reg signed[17:0] wd=0;
 transition_controller control(clk,rst_n,iv,fv,pv,pb,pa,ob,oe,nb,ne,fade,alpha,occupied,commits,av,ver);
 fir_mac_core fade_core(clk,rst_n,iv,x,seq,fr,ob,oe,nb,ne,alpha,fade,
   we,wb,wa,wd,fv,fy,fs,fd,fc,fm,fb);
 wire[1:0] hard_bank=fade ? nb:ob;
 wire hard_enable=fade ? ne:oe;
 fir_mac_core hard_core(clk,rst_n,iv,x,seq,hr,hard_bank,hard_enable,2'd0,1'b0,6'd0,1'b0,
   we,wb,wa,wd,hv,hy,hs,hd,hc,hm,hb);
 reg gs=0;reg[31:0] gw=0;wire gb,gd,ge,cv;wire[8:0] ca;wire signed[17:0] cd;
 wire[31:0] gc;wire[63:0] norm;
 coefficient_generator #(.GRID_ALIGNED(1)) generator(clk,rst_n,gs,gw,gb,gd,ge,cv,ca,cd,gc,norm);
 reg signed[17:0] c1[0:320],c2[0:320];
 reg signed[15:0] samples[0:8191];
 integer clk_count=0,fh,eh,ch,i,j,q,which=0,done_count=0;
 always @(posedge clk)begin
   clk_count=clk_count+1;
   if(cv)begin if(which==0)c1[ca]=cd;else c2[ca]=cd;end
   if(fv)begin $fwrite(fh,"%0d,%0d,fade,%0d,%0d\n",q,fs,clk_count,$signed(fy));done_count=done_count+1;end
   if(hv)$fwrite(fh,"%0d,%0d,hard,%0d,%0d\n",q,hs,clk_count,$signed(hy));
   if(iv)begin
     if(!fr || !hr)$fatal(1,"sample deadline missed");
     $fwrite(eh,"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d\n",q,seq,clk_count,pa,fade,alpha,ob,nb,oe);
   end
 end
 task generate_correction;
   input integer slot;input[31:0] word;
   begin
     @(negedge clk);which=slot;gw=word;gs=1;
     @(negedge clk);gs=0;
     wait(gd);@(negedge clk);
     if(ge)$fatal(1,"coefficient generator error");
   end
 endtask
 initial begin
   fh=$fopen("outputs.csv","w");eh=$fopen("events.csv","w");ch=$fopen("coefficients.csv","w");
   $fwrite(fh,"case,sample,mode,clock,output\n");
   $fwrite(eh,"case,sample,clock,proposal_accept,fading,alpha,old_bank,new_bank,old_enable\n");
   $fwrite(ch,"tap,bank1,bank2\n");
   $readmemh("input.hex",samples);
   repeat(5)@(negedge clk);rst_n=1;
   // Frequencies 2800.78125 and 2925.78125 Hz, exactly the production FCW grid.
   generate_correction(0,32'd375914496);
   generate_correction(1,32'd392691712);
   for(i=0;i<321;i=i+1)begin
     $fwrite(ch,"%0d,%0d,%0d\n",i,(i==320?65536:0)-c1[i],(i==320?65536:0)-c1[i]-c2[i]);
     @(negedge clk);we=1;wb=1;wa=i;wd=(i==320?65536:0)-c1[i];
     @(negedge clk);wb=2;wd=(i==320?65536:0)-c1[i]-c2[i];
   end
   @(negedge clk);we=0;
   for(q=0;q<2;q=q+1)begin
     rst_n=0;iv=0;pv=0;repeat(5)@(negedge clk);rst_n=1;done_count=0;
     for(j=0;j<4096;j=j+1)begin
       @(negedge clk);iv=1;x=samples[q*4096+j];seq=j;
       pv=(j==800 || j==2304);pb=j==800?1:2;ver=j==800?1:2;
       @(negedge clk);iv=0;pv=0;
       // Exact 50 MHz / 32 kHz cadence: alternate 1562 and 1563 clocks.
       repeat((j%2==0?1562:1563)-2)@(negedge clk);
     end
     repeat(1600)@(negedge clk);
     if(done_count!=4096 || fd || hd || fc || hc || commits!=2)
       $fatal(1,"integrity failure count=%0d fd=%0d hd=%0d fc=%0d hc=%0d commits=%0d",done_count,fd,hd,fc,hc,commits);
     $display("CASE %0d PASS outputs=%0d fade_cycles=%0d hard_cycles=%0d",q,done_count,fm,hm);
   end
   $fclose(fh);$fclose(eh);$fclose(ch);$display("PASS controlled switching");$finish;
 end
 initial begin #300000000;$fatal(1,"timeout");end
endmodule
