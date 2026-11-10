obj-m += mxo2.o
mxo2-y := mxo2_main.o mxo2_capture.o mxo2_rx.o mxo2_v4l2.o mxo2_audio.o mxo2_output.o

KDIR ?= /lib/modules/$(shell uname -r)/build

all:
	$(MAKE) -C $(KDIR) M=$(CURDIR) modules

clean:
	$(MAKE) -C $(KDIR) M=$(CURDIR) clean
