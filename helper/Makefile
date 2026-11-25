CC      ?= cc
CFLAGS  ?= -O2 -Wall -Wextra
CFLAGS  += -std=gnu11 -Invidia-headers

nvkms-vibrance: nvkms-vibrance.c $(wildcard nvidia-headers/*.h)
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ nvkms-vibrance.c

clean:
	rm -f nvkms-vibrance

.PHONY: clean
