#!

# Backend Protocol Decoder (No UI or Graph). This can be run by itself as a headless tool to capture data to csv format.
#python opentms_protocol.py /dev/cu.usbmodem210836601 --seconds 10 --raw-csv raw.csv

python3 serial_plot_pyqtgraph.py /dev/cu.usbmodem210836601 --fast-hz 60

# Additional Options:
#   --raw --raw-csv run1.csv logs the integer ADC data.
#   --save-csv fast.csv logs the plotted voltages.
#   --hline dac_threshold_cap_v draws the measured comparator threshold on the graph.
#   --help lists everything else.
