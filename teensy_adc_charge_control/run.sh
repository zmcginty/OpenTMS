#!
#python -u serial_plot_teensy_adc.py --window 1000 --max-abs-volts 6 --max-jump 0.7 --single 

#--save-csv test-run

#python serial_plot_pyqtgraph.py --single --max-abs-volts 1 --max-jump 0.01 --debug
python serial_plot_pyqtgraph.py /dev/cu.usbmodem176191001 --single --min-yspan 0.001 --debug --save-csv test-run2
