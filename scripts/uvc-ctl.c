// Read or set one UVC control on a USB camera on macOS, while the camera is streaming.
//
// The Reachy Mini camera's factory image is dark (brightness 0, range -64..64) and flat;
// scripts/start-reachy.sh builds this helper and tunes it at launch. libusb cannot do
// this because UVCAssistant holds the device; an interface control request through
// IOKit still works. A control is 2 bytes unless the selector says otherwise
// (selector:bytes, 1 to 4).
//
//   uvc-ctl <vid> <pid> <unit> <selector>[:<bytes>] get|min|max|def|set [value]
//   uvc-ctl 0x38fb 0x1002 2 2 set 32    # Reachy Mini camera: processing unit 2, brightness
//   uvc-ctl 0x38fb 0x1002 1 3:1 set 1   # camera terminal 1, auto-exposure priority (1 byte)
//
// Build: clang -O2 -o uvc-ctl uvc-ctl.c -framework IOKit -framework CoreFoundation

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOKitLib.h>
#include <IOKit/IOCFPlugIn.h>
#include <IOKit/usb/IOUSBLib.h>

enum { UVC_SET_CUR = 0x01, UVC_GET_CUR = 0x81, UVC_GET_MIN = 0x82, UVC_GET_MAX = 0x83, UVC_GET_DEF = 0x87 };

static void set_int(CFMutableDictionaryRef dict, const char *key, int value) {
    CFStringRef cf_key = CFStringCreateWithCString(NULL, key, kCFStringEncodingUTF8);
    CFNumberRef cf_value = CFNumberCreate(NULL, kCFNumberIntType, &value);
    CFDictionarySetValue(dict, cf_key, cf_value);
    CFRelease(cf_key);
    CFRelease(cf_value);
}

// Return the camera's video control interface (class 14, subclass 1), or NULL.
static IOUSBInterfaceInterface190 **open_video_control(int vid, int pid) {
    CFMutableDictionaryRef match = IOServiceMatching("IOUSBHostInterface");
    CFMutableDictionaryRef props = CFDictionaryCreateMutable(
        NULL, 0, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    set_int(props, "idVendor", vid);
    set_int(props, "idProduct", pid);
    set_int(props, "bInterfaceClass", 14);
    set_int(props, "bInterfaceSubClass", 1);
    CFDictionarySetValue(match, CFSTR(kIOPropertyMatchKey), props);
    CFRelease(props);

    io_service_t service = IOServiceGetMatchingService(kIOMainPortDefault, match);
    if (!service) return NULL;
    IOCFPlugInInterface **plugin = NULL;
    SInt32 score = 0;
    kern_return_t kr = IOCreatePlugInInterfaceForService(
        service, kIOUSBInterfaceUserClientTypeID, kIOCFPlugInInterfaceID, &plugin, &score);
    IOObjectRelease(service);
    if (kr != KERN_SUCCESS || !plugin) return NULL;
    IOUSBInterfaceInterface190 **intf = NULL;
    (*plugin)->QueryInterface(plugin, CFUUIDGetUUIDBytes(kIOUSBInterfaceInterfaceID190), (LPVOID *)&intf);
    (*plugin)->Release(plugin);
    return intf;
}

int main(int argc, char **argv) {
    if (argc < 6 || (strcmp(argv[5], "set") == 0 && argc < 7)) {
        fprintf(stderr, "usage: %s <vid> <pid> <unit> <selector>[:<bytes>] get|min|max|def|set [value]\n", argv[0]);
        return 2;
    }
    int vid = (int)strtol(argv[1], NULL, 0);
    int pid = (int)strtol(argv[2], NULL, 0);
    int unit = (int)strtol(argv[3], NULL, 0);
    char *size_text = NULL;
    int selector = (int)strtol(argv[4], &size_text, 0);
    int size = *size_text == ':' ? (int)strtol(size_text + 1, NULL, 0) : 2;
    if (size < 1 || size > 4) {
        fprintf(stderr, "control size must be 1 to 4 bytes: %s\n", argv[4]);
        return 2;
    }
    const char *op = argv[5];

    UInt8 request;
    if (strcmp(op, "set") == 0) request = UVC_SET_CUR;
    else if (strcmp(op, "get") == 0) request = UVC_GET_CUR;
    else if (strcmp(op, "min") == 0) request = UVC_GET_MIN;
    else if (strcmp(op, "max") == 0) request = UVC_GET_MAX;
    else if (strcmp(op, "def") == 0) request = UVC_GET_DEF;
    else {
        fprintf(stderr, "unknown operation: %s\n", op);
        return 2;
    }

    IOUSBInterfaceInterface190 **intf = open_video_control(vid, pid);
    if (!intf) {
        fprintf(stderr, "camera %04x:%04x video control interface not found\n", vid, pid);
        return 3;
    }
    UInt8 interface_number = 0;
    (*intf)->GetInterfaceNumber(intf, &interface_number);

    // Little-endian, as UVC sends it; 2-byte values (brightness) are signed.
    int32_t value = request == UVC_SET_CUR ? (int32_t)strtol(argv[6], NULL, 0) : 0;
    IOUSBDevRequest req = {0};
    req.bmRequestType = request == UVC_SET_CUR
        ? USBmakebmRequestType(kUSBOut, kUSBClass, kUSBInterface)
        : USBmakebmRequestType(kUSBIn, kUSBClass, kUSBInterface);
    req.bRequest = request;
    req.wValue = (UInt16)(selector << 8);
    req.wIndex = (UInt16)((unit << 8) | interface_number);
    req.wLength = (UInt16)size;
    req.pData = &value;
    kern_return_t kr = (*intf)->ControlRequest(intf, 0, &req);
    (*intf)->Release(intf);
    if (kr != kIOReturnSuccess) {
        fprintf(stderr, "UVC control request failed: 0x%x\n", kr);
        return 4;
    }
    if (request != UVC_SET_CUR) {
        if (size == 1) value &= 0xff;
        else if (size == 2) value = (int16_t)(value & 0xffff);
        else if (size == 3) value &= 0xffffff;
        printf("%d\n", value);
    }
    return 0;
}
