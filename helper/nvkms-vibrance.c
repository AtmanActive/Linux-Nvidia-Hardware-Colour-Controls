/*
 * nvkms-vibrance: read and set NVIDIA Digital Vibrance in the display
 * hardware through the NVKMS ioctl interface (/dev/nvidia-modeset).
 *
 * Works on Wayland and X11 alike, because it talks to the kernel driver
 * directly rather than through the display server. Digital Vibrance is
 * applied by the display engine's output colour-space converter, so it is a
 * hardware adjustment (no shaders, no extra copies of the frame).
 *
 * Inspired by nvibrant (https://github.com/Tremeschin/nvibrant, GPL-3.0),
 * but written independently against the NVKMS headers from
 * NVIDIA/open-gpu-kernel-modules (MIT).
 *
 * Usage:
 *   nvkms-vibrance list                     JSON description of all ports
 *   nvkms-vibrance get <disp> <dpyId>       read vibrance of one display
 *   nvkms-vibrance set <disp> <dpyId> <v>   set vibrance (-1024..1023)
 *
 * Environment:
 *   NVIDIA_GPU                  GPU index to open (default 0)
 *   NVIDIA_DRIVER_VERSION       override the detected driver version string
 */

#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

#include "nvkms-api.h"
#include "nvkms-ioctl.h"

static int nvkms_fd = -1;

static int nvkms_ioctl(NvU32 cmd, void *data, NvU32 size)
{
    struct NvKmsIoctlParams params;
    memset(&params, 0, sizeof(params));
    params.cmd = cmd;
    params.size = size;
    params.address = (NvU64)(uintptr_t)data;
    return ioctl(nvkms_fd, NVKMS_IOCTL_IOWR, &params);
}

#define NVKMS(cmd, p) nvkms_ioctl((cmd), (p), sizeof(*(p)))

static const char *driver_version(void)
{
    static char version[NVKMS_NVIDIA_DRIVER_VERSION_STRING_LENGTH];
    const char *env = getenv("NVIDIA_DRIVER_VERSION");
    if (env && *env) {
        snprintf(version, sizeof(version), "%s", env);
        return version;
    }
    FILE *f = fopen("/sys/module/nvidia/version", "r");
    if (!f)
        return NULL;
    if (!fgets(version, sizeof(version), f)) {
        fclose(f);
        return NULL;
    }
    fclose(f);
    version[strcspn(version, "\r\n")] = '\0';
    return version;
}

static const char *connector_type(NvU32 type)
{
    switch (type) {
    case NVKMS_CONNECTOR_TYPE_DP:    return "DP";
    case NVKMS_CONNECTOR_TYPE_VGA:   return "VGA";
    case NVKMS_CONNECTOR_TYPE_DVI_I: return "DVI-I";
    case NVKMS_CONNECTOR_TYPE_DVI_D: return "DVI-D";
    case NVKMS_CONNECTOR_TYPE_ADC:   return "ADC";
    case NVKMS_CONNECTOR_TYPE_LVDS:  return "LVDS";
    case NVKMS_CONNECTOR_TYPE_HDMI:  return "HDMI";
    case NVKMS_CONNECTOR_TYPE_USBC:  return "USB-C";
    case NVKMS_CONNECTOR_TYPE_DSI:   return "DSI";
    default:                         return "unknown";
    }
}

static void json_string(const char *s)
{
    putchar('"');
    for (; *s; s++) {
        unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\')
            printf("\\%c", c);
        else if (c < 0x20)
            printf("\\u%04x", c);
        else
            putchar(c);
    }
    putchar('"');
}

static int open_device(struct NvKmsAllocDeviceParams *alloc)
{
    const char *version = driver_version();
    if (!version) {
        fprintf(stderr, "error: cannot read /sys/module/nvidia/version\n");
        return 2;
    }

    nvkms_fd = open("/dev/nvidia-modeset", O_RDWR | O_CLOEXEC);
    if (nvkms_fd < 0) {
        fprintf(stderr, "error: cannot open /dev/nvidia-modeset: %s\n", strerror(errno));
        return 2;
    }

    memset(alloc, 0, sizeof(*alloc));
    const char *gpu = getenv("NVIDIA_GPU");
    alloc->request.deviceId.rmDeviceId = gpu ? (NvU32)atoi(gpu) : 0;
    snprintf(alloc->request.versionString, sizeof(alloc->request.versionString), "%s", version);
    alloc->request.no3d = NV_TRUE;

    if (NVKMS(NVKMS_IOCTL_ALLOC_DEVICE, alloc) < 0 ||
        alloc->reply.status != NVKMS_ALLOC_DEVICE_STATUS_SUCCESS) {
        if (alloc->reply.status == NVKMS_ALLOC_DEVICE_STATUS_VERSION_MISMATCH)
            fprintf(stderr, "error: NVKMS version mismatch (driver %s); rebuild the helper "
                            "against matching headers or reboot after a driver update\n", version);
        else
            fprintf(stderr, "error: NVKMS ALLOC_DEVICE failed (status %d, %s); the helper may "
                            "need rebuilding for driver %s\n",
                    (int)alloc->reply.status, strerror(errno), version);
        return 3;
    }
    return 0;
}

static void close_device(struct NvKmsAllocDeviceParams *alloc)
{
    struct NvKmsFreeDeviceParams freeDev;
    memset(&freeDev, 0, sizeof(freeDev));
    freeDev.request.deviceHandle = alloc->reply.deviceHandle;
    NVKMS(NVKMS_IOCTL_FREE_DEVICE, &freeDev);
    close(nvkms_fd);
}

static int get_attr(struct NvKmsAllocDeviceParams *alloc, NvU32 disp, NVDpyId dpy,
                    enum NvKmsDpyAttribute attr, NvS64 *value)
{
    struct NvKmsGetDpyAttributeParams p;
    memset(&p, 0, sizeof(p));
    p.request.deviceHandle = alloc->reply.deviceHandle;
    p.request.dispHandle = alloc->reply.dispHandles[disp];
    p.request.dpyId = dpy;
    p.request.attribute = attr;
    if (NVKMS(NVKMS_IOCTL_GET_DPY_ATTRIBUTE, &p) < 0)
        return -1;
    *value = p.reply.value;
    return 0;
}

static int cmd_list(struct NvKmsAllocDeviceParams *alloc)
{
    printf("{\"driver\":");
    json_string(driver_version());
    printf(",\"ports\":[");
    int first = 1;

    for (NvU32 disp = 0; disp < alloc->reply.numDisps; disp++) {
        struct NvKmsQueryDispParams qd;
        memset(&qd, 0, sizeof(qd));
        qd.request.deviceHandle = alloc->reply.deviceHandle;
        qd.request.dispHandle = alloc->reply.dispHandles[disp];
        if (NVKMS(NVKMS_IOCTL_QUERY_DISP, &qd) < 0)
            continue;

        for (NvU32 c = 0; c < qd.reply.numConnectors; c++) {
            struct NvKmsQueryConnectorStaticDataParams sd;
            memset(&sd, 0, sizeof(sd));
            sd.request.deviceHandle = alloc->reply.deviceHandle;
            sd.request.dispHandle = alloc->reply.dispHandles[disp];
            sd.request.connectorHandle = qd.reply.connectorHandles[c];
            if (NVKMS(NVKMS_IOCTL_QUERY_CONNECTOR_STATIC_DATA, &sd) < 0)
                continue;

            static struct NvKmsQueryDpyDynamicDataParams dd;
            memset(&dd, 0, sizeof(dd));
            dd.request.deviceHandle = alloc->reply.deviceHandle;
            dd.request.dispHandle = alloc->reply.dispHandles[disp];
            dd.request.dpyId = sd.reply.dpyId;
            int have_dyn = NVKMS(NVKMS_IOCTL_QUERY_DPY_DYNAMIC_DATA, &dd) >= 0;

            NvBool connected = have_dyn && dd.reply.connected;
            printf("%s{\"disp\":%u,\"index\":%u,\"dpyId\":%u,\"type\":", first ? "" : ",",
                   disp, c, nvDpyIdToNvU32(sd.reply.dpyId));
            json_string(connector_type(sd.reply.type));
            printf(",\"name\":");
            json_string(have_dyn ? dd.reply.name : "");
            printf(",\"connected\":%s", connected ? "true" : "false");

            printf(",\"edid\":\"");
            if (connected && dd.reply.edid.valid) {
                for (NvU32 i = 0; i < dd.reply.edid.bufferSize && i < NVKMS_EDID_BUFFER_SIZE; i++)
                    printf("%02x", dd.reply.edid.buffer[i]);
            }
            putchar('"');

            /* The API head equals the DRM CRTC index created by nvidia-drm,
             * which is how the caller maps NVKMS ports to DRM connectors. */
            NvS64 v;
            if (connected && get_attr(alloc, disp, sd.reply.dpyId,
                                      NV_KMS_DPY_ATTRIBUTE_HEAD, &v) == 0 && v >= 0 &&
                v != NV_INVALID_HEAD)
                printf(",\"head\":%lld", (long long)v);
            else
                printf(",\"head\":null");

            if (connected && get_attr(alloc, disp, sd.reply.dpyId,
                                      NV_KMS_DPY_ATTRIBUTE_DIGITAL_VIBRANCE, &v) == 0)
                printf(",\"vibrance\":%lld", (long long)v);
            else
                printf(",\"vibrance\":null");
            putchar('}');
            first = 0;
        }
    }
    printf("]}\n");
    return 0;
}

static int print_vibrance(struct NvKmsAllocDeviceParams *alloc, NvU32 disp, NvU32 dpy)
{
    NvS64 v;
    if (get_attr(alloc, disp, nvNvU32ToDpyId(dpy), NV_KMS_DPY_ATTRIBUTE_DIGITAL_VIBRANCE,
                 &v) == 0) {
        printf("{\"vibrance\":%lld}\n", (long long)v);
        return 0;
    }
    printf("{\"vibrance\":null}\n");
    return 1;
}

static int cmd_get(struct NvKmsAllocDeviceParams *alloc, NvU32 disp, NvU32 dpy)
{
    if (disp >= alloc->reply.numDisps) {
        fprintf(stderr, "error: no disp %u\n", disp);
        return 1;
    }
    return print_vibrance(alloc, disp, dpy);
}

static int cmd_set(struct NvKmsAllocDeviceParams *alloc, NvU32 disp, NvU32 dpy, long value)
{
    if (disp >= alloc->reply.numDisps) {
        fprintf(stderr, "error: no disp %u\n", disp);
        return 1;
    }
    if (value < -1024) value = -1024;
    if (value > 1023) value = 1023;

    struct NvKmsSetDpyAttributeParams p;
    memset(&p, 0, sizeof(p));
    p.request.deviceHandle = alloc->reply.deviceHandle;
    p.request.dispHandle = alloc->reply.dispHandles[disp];
    p.request.dpyId = nvNvU32ToDpyId(dpy);
    p.request.attribute = NV_KMS_DPY_ATTRIBUTE_DIGITAL_VIBRANCE;
    p.request.value = value;
    if (NVKMS(NVKMS_IOCTL_SET_DPY_ATTRIBUTE, &p) < 0) {
        fprintf(stderr, "error: SET_DPY_ATTRIBUTE failed: %s\n", strerror(errno));
        return 1;
    }

    print_vibrance(alloc, disp, dpy);
    return 0;
}

static int usage(const char *argv0)
{
    fprintf(stderr, "usage: %s list | get <disp> <dpyId> | set <disp> <dpyId> <value>\n", argv0);
    return 1;
}

int main(int argc, char **argv)
{
    if (argc < 2)
        return usage(argv[0]);
    const char *cmd = argv[1];
    if (!((!strcmp(cmd, "list") && argc == 2) || (!strcmp(cmd, "get") && argc == 4) ||
          (!strcmp(cmd, "set") && argc == 5)))
        return usage(argv[0]);

    struct NvKmsAllocDeviceParams alloc;
    int rc = open_device(&alloc);
    if (rc)
        return rc;

    if (!strcmp(cmd, "list"))
        rc = cmd_list(&alloc);
    else if (!strcmp(cmd, "get"))
        rc = cmd_get(&alloc, (NvU32)strtoul(argv[2], NULL, 10),
                     (NvU32)strtoul(argv[3], NULL, 10));
    else
        rc = cmd_set(&alloc, (NvU32)strtoul(argv[2], NULL, 10),
                     (NvU32)strtoul(argv[3], NULL, 10), strtol(argv[4], NULL, 10));

    close_device(&alloc);
    return rc;
}
