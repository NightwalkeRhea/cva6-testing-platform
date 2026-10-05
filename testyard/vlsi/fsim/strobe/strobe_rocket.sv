`ifndef TOPLEVEL
	`define TOPLEVEL TestDriver.testHarness.chiptop0
`endif

module strobe;

`ifndef START_INJECTION_LABEL 
`define START_INJECTION_LABEL 32'h80000230
`endif /* START_INJECTION_LABEL */

initial begin        
  #`RESET_DELAY;
  $display("BEFORE ZOIX INJECTION");
  wait(`TOPLEVEL.system.tile_prci_domain.element_reset_domain_rockettile.core.wb_reg_pc[31:0] == `START_INJECTION_LABEL);
  $fs_inject;
  $display("ZOIX INJECTION");
end

always @(negedge `TOPLEVEL.clock_uncore) begin
  $fs_strobe(`TOPLEVEL.system.tile_prci_domain.element_reset_domain_rockettile.core);
end 
endmodule
