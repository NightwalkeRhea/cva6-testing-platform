// The complete path to ChipTop in the simulation harness.
`ifndef TOPLEVEL
  `define TOPLEVEL TestDriver.testHarness.chiptop0
`endif

module strobe;

  // The evaluator reads this address from each compiled candidate.
  logic [63:0] raw_body_pc;
  logic [63:0] matched_pc;
  bit commit_hit;
  int commit_port;
  longint unsigned cycle_count = 0;

  // Count cycles so the saved marker shows when injection happened.
  always @(posedge `TOPLEVEL.clock_uncore) begin
    cycle_count <= cycle_count + 1;
  end

  // Inject when the first instruction of raw_body retires.
  initial begin
    #`RESET_DELAY;

    // A missing address must fail instead of producing empty coverage.
    if (!$value$plusargs("raw_body_pc=%h", raw_body_pc)) begin
      $fatal(1, "CVA6 RAW: missing +raw_body_pc");
    end

    // CVA6 can retire on either of its two commit ports.
    do begin
      // Retirement is valid at the active clock edge.
      @(posedge `TOPLEVEL.clock_uncore);
      commit_hit = 1'b0;

      if ((`TOPLEVEL.system.tile_prci_domain
           .element_reset_domain_cva6_tile.core.i_ariane.i_cva6.commit_ack[0] === 1'b1) &&
          (`TOPLEVEL.system.tile_prci_domain
           .element_reset_domain_cva6_tile.core.i_ariane.i_cva6
           .commit_instr_id_commit[0].pc === raw_body_pc)) begin
        commit_hit = 1'b1;
        commit_port = 0;
        matched_pc = `TOPLEVEL.system.tile_prci_domain
                     .element_reset_domain_cva6_tile.core.i_ariane.i_cva6
                     .commit_instr_id_commit[0].pc;
      end else if ((`TOPLEVEL.system.tile_prci_domain
                    .element_reset_domain_cva6_tile.core.i_ariane.i_cva6
                    .commit_ack[1] === 1'b1) &&
                   (`TOPLEVEL.system.tile_prci_domain
                    .element_reset_domain_cva6_tile.core.i_ariane.i_cva6
                    .commit_instr_id_commit[1].pc === raw_body_pc)) begin
        commit_hit = 1'b1;
        commit_port = 1;
        matched_pc = `TOPLEVEL.system.tile_prci_domain
                     .element_reset_domain_cva6_tile.core.i_ariane.i_cva6
                     .commit_instr_id_commit[1].pc;
      end
    end while (commit_hit !== 1'b1);

    $display(
      "CVA6 RAW: injecting faults at cycle %0d, commit port %0d, raw_body PC 0x%016h",
      cycle_count,
      commit_port,
      matched_pc
    );
    $fs_inject;
  end

  // Observe traffic at the CVA6 wrapper after every settled cycle.
  // Internal probe ports are not visible at this boundary.
  always @(negedge `TOPLEVEL.clock_uncore) begin
    $fs_strobe(
      `TOPLEVEL.system.tile_prci_domain
      .element_reset_domain_cva6_tile.core
    );
  end

endmodule
