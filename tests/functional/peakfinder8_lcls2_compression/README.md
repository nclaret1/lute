# Peakfinder8 (V1) LCLS2 Data + ROIBinSz

This example runs the peakfinder8 (original V1) algorithm on LCLS2 data. The experiment/run were chosen at random and may not have actual data to analyze. This test verifies the infrastructure. It additionally incorporates the compress/decompress tests with ROIBinSz.

NOTE: This test is also a good test of the first-party `Task` running in a different environment. The `PeakFinderSFXPressio` **managed** `Task` uses a different environment than the base one used to run the infrastructure (as that environment is not generally required to have `libpressio` yet).
