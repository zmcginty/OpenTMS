#!
python dscope_tms.py measure --ref-type current --ch1-factor 10000 --ch1-vdiv 50 \      (base)
                                                             --ch0-vdiv 20 --trig-src ch1 --trig-level -200 --slope falling \
                                                             --na 6.5e-5 --count 5 --plot --interval 3 \
                                                             --fire-serial /dev/tty.usbmodem135770201 --fire-cmd "F"
