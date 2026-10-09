#################################
# Build the RAW hazard test
#################################

cmake_minimum_required(VERSION 3.10)

# Build one small program that stresses RAW dependencies in CVA6.
add_executable(raw-hazard raw-hazard.c)

# Also produce raw-hazard.dump so we can inspect the exact instructions.
add_dump_target(raw-hazard)
