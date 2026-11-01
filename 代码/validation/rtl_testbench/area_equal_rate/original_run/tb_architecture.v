`timescale 1ns/1ps
module tb_architecture;
 parameter K=4,N=1536;
 reg clk=0;always #10 clk=~clk;
 reg rst_n=0,iv=0;reg signed[15:0] x;reg[31:0] seq=0;
 reg fade=0;reg[5:0] alpha=0;reg we=0;reg[1:0] bank;
 reg[2:0] branch;reg[8:0] addr;reg signed[17:0] md,bd;
 wire mr,mo,mbusy,br,bo,bbusy;wire signed[15:0] my,by;
 wire[31:0] ms,bs,drop,sat;wire[15:0] mc,bc;
 reg[1:0] ob=0,nb=1;
 fir_mac_core merged(clk,rst_n,iv,x,seq,mr,ob,1'b1,nb,1'b1,alpha,fade,
   we,bank,addr,md,mo,my,ms,drop,sat,mc,mbusy);
 wire[31:0] bdrop,bsat;
 dual_lane_correction_mac separate(clk,rst_n,iv,x,seq,fade,ob[0],alpha,we,branch,addr,bd,br,bo,by,bs,bdrop,bsat,bc,bbusy);
 reg[17:0] merged_coeff[0:641];reg[17:0] corrections[0:2567];reg[15:0] samples[0:N-1];
 integer i,j,clock_count=0,start_clock,merged_clock,branch_clock;
 integer products_m=0,products_b=0,normal_m=0,normal_b=0,fade_m=0,fade_b=0;
 integer f;
 reg gotm=0,gotb=0;reg signed[15:0] ym,yb;
 initial begin
   $readmemh("merged.mem",merged_coeff);$readmemh("corrections.mem",corrections);
   $readmemh("input.mem",samples);f=$fopen("outputs.csv","w");
   $fdisplay(f,"sample,input,merged,independent,fade,alpha,merged_cycles,independent_cycles,merged_products,independent_products");
   repeat(4)@(negedge clk);rst_n=1;
   for(j=0;j<2*K;j=j+1)for(i=0;i<321;i=i+1)begin
     we=1;branch=j;addr=i;bd=corrections[j*321+i];bank=j/K;md=merged_coeff[(j/K)*321+i];@(negedge clk);
   end
   we=0;
   for(i=0;i<N;i=i+1)begin
     wait(mr&&br);@(negedge clk);seq=i;x=samples[i];
     fade=i>=1024&&i<1056;alpha=fade?i-1023:0;
     ob=i>=1056?1:0;nb=1;gotm=0;gotb=0;products_m=0;products_b=0;
     iv=1;@(negedge clk);iv=0;
     wait(gotm&&gotb);@(negedge clk);
     if(ym!==yb)$fatal(1,"K%0d sample%0d merged%0d branch%0d",K,i,ym,yb);
     $fdisplay(f,"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",i,$signed(samples[i]),ym,yb,fade,alpha,
       merged_clock-start_clock,branch_clock-start_clock,products_m,products_b);
     if(fade)begin if(merged_clock-start_clock>fade_m)fade_m=merged_clock-start_clock;
       if(branch_clock-start_clock>fade_b)fade_b=branch_clock-start_clock;end
     else begin if(merged_clock-start_clock>normal_m)normal_m=merged_clock-start_clock;
       if(branch_clock-start_clock>normal_b)normal_b=branch_clock-start_clock;end
   end
   if(drop!=0||bdrop!=0)$fatal(1,"unexpected dropped input");
   if(bc>1562||mc>1562)$fatal(1,"sample service deadline");
   if(sat!==bsat)$fatal(1,"saturation counter mismatch");
   if(mc!=fade_m||bc!=fade_b)$fatal(1,"timestamp/max_cycles disagreement merged=%0d/%0d branch=%0d/%0d",mc,fade_m,bc,fade_b);
   $display("ARCH K=%0d PASS samples=%0d normal=%0d/%0d fade=%0d/%0d products=%0d/%0d",K,N,normal_m,normal_b,fade_m,fade_b,products_m,products_b);
   $fclose(f);$finish;
 end
 always @(posedge clk)begin
   clock_count=clock_count+1;
   if(iv&&mr&&br)start_clock=clock_count;
 end
 always @(negedge clk)begin
   if(merged.state==2)products_m=products_m+1;
   if(separate.read_valid)products_b=products_b+2;
   if(mo)begin gotm=1;ym=my;merged_clock=clock_count;end
   if(bo)begin gotb=1;yb=by;branch_clock=clock_count;end
 end
 initial begin #2000000000;$fatal(1,"timeout");end
endmodule
