# GCC profile compatibility

The unchanged control flow and counters may share a baseline profile. Added
source lines trigger a GCC location-checksum warning; GCC 13.3
[get_coverage_counts](https://raw.githubusercontent.com/gcc-mirror/gcc/releases/gcc-13.3.0/gcc/coverage.cc)
returns counts after this warning. CFG or counter-count mismatches return no
counts. The worker permits and records only source-location diagnostics; missing
profiles and all structural mismatches remain fatal. Counter files stay immutable.
The default-error attempt failed before timing and is preserved privately.
