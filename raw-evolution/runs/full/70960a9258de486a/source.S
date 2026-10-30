    # Emit normal code without position-independent addressing.
    .option nopic
    .text
    .align 2
    # Let the C harness call this symbol
    .globl raw_body
    .type raw_body, @function

# A small program with several dependency distances
raw_body:
    # Reserve one stack slot and save the input there
    addi sp, sp, -16
    sd a0, 0(sp)
    # An adjacent producer and consumer, so t1 reads t0 immediately
    addi t0, a0, 17
    xor  t1, t0, a0
    # now one independent instruction in between
    slli t2, t1, 3
    addi t6, a0, 5
    add  t3, t2, t6
    # now dependency on both rs1 and rs2, so t5 has to read "recent" t4 and t3
    sub  t4, t3, t1
    xor  t5, t4, t3
    # A load, followed by ALU instruction to consume the load result
    ld   t0, 0(sp)
    addi t1, t0, 31
    # Return a value that depends on all main chains above
    xor  a0, t5, t1
    # Put the running value in memory.
    sd a0, 0(sp)
    # Load that value into t0.
    lbu t0, 0(sp)
    # Consume the load result immediately.
    sll t1, t0, a0
    # Feed the result into later blocks.
    xor a0, a0, t1
    # Make a new value in t0.
    addi t0, a0, -1948
    # Read t0 without any gap.
    slt t1, t0, a0
    # Keep this block connected to the next block.
    xor a0, a0, t1
    # Start a multiply operation.
    mulh t0, a0, a0
    # Consume the multiply result immediately.
    srl t1, t0, a0
    # Keep the result alive for later patterns.
    xor a0, a0, t1
    # Make the first source.
    addi t0, a0, -1808
    # Make the second source.
    xori t1, a0, -205
    # Read both recent sources together.
    sll t2, t0, t1
    # Preserve an effect from this block.
    xor a0, a0, t2
    # Make a new value in t2.
    addi t2, a0, -457
    # Insert one independent instruction.
    addi t4, a0, 1
    # Read t2 after the one-instruction gap.
    sra t3, t2, t4
    # Mix the result into the running value.
    xor a0, a0, t3
    # Make the first source.
    addi t0, a0, 1009
    # Make the second source.
    xori t1, a0, -685
    # Read both recent sources together.
    xor t2, t0, t1
    # Preserve an effect from this block.
    xor a0, a0, t2
    # Release the stack slot before returning to C
    addi sp, sp, 16
    ret
    # Record the final size for the assembler and linker
    .size raw_body, .-raw_body
