// The complete path to ChipTop in the simulation harness.
`ifndef TOPLEVEL
  `define TOPLEVEL TestDriver.testHarness.chiptop0
`endif

module strobe;

  // Inject after reset
  // This simple trigger is enough for the first campaign.
  initial begin
    #`RESET_DELAY;

    // Wait a few cycles so the design can leave reset cleanly.
    repeat (10) @(negedge `TOPLEVEL.clock_uncore);

    $display("CVA6: injecting faults");
    $fs_inject;
  end

  // Observe both boundaries after every settled cycle.
  always @(negedge `TOPLEVEL.clock_uncore) begin
    $fs_strobe(
      `TOPLEVEL.system.tile_prci_domain
      .element_reset_domain_cva6_tile.core
      .i_ariane.i_cva6.issue_stage_i.i_scoreboard
    );

    $fs_strobe(
      `TOPLEVEL.system.tile_prci_domain
      .element_reset_domain_cva6_tile.core
      .i_ariane.i_cva6.issue_stage_i.i_issue_read_operands
    );
  end

endmodule