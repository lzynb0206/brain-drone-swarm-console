#import <Foundation/Foundation.h>
#import <IOBluetooth/IOBluetooth.h>
#include <unistd.h>

@interface RFCOMMBridge : NSObject <IOBluetoothRFCOMMChannelDelegate>
@property(nonatomic, strong) IOBluetoothRFCOMMChannel *channel;
@property(nonatomic, strong) NSFileHandle *standardInput;
@property(nonatomic, assign) BOOL finished;
@end

@implementation RFCOMMBridge

- (void)startReadingCommands {
    self.standardInput = [NSFileHandle fileHandleWithStandardInput];
    [[NSNotificationCenter defaultCenter]
        addObserver:self
           selector:@selector(receivedCommandData:)
               name:NSFileHandleReadCompletionNotification
             object:self.standardInput];
    [self.standardInput readInBackgroundAndNotify];
}

- (void)receivedCommandData:(NSNotification *)notification {
    NSData *data = notification.userInfo[NSFileHandleNotificationDataItem];
    if (data.length == 0) {
        self.finished = YES;
        [self.channel closeChannel];
        return;
    }
    const uint8_t *bytes = data.bytes;
    NSUInteger offset = 0;
    NSUInteger mtu = MAX((NSUInteger)1, (NSUInteger)self.channel.getMTU);
    while (offset < data.length) {
        UInt16 length = (UInt16)MIN(mtu, data.length - offset);
        IOReturn status = [self.channel writeSync:(void *)(bytes + offset) length:length];
        if (status != kIOReturnSuccess) {
            fprintf(stderr, "WRITE_ERROR status=0x%08x\n", status);
            fflush(stderr);
            self.finished = YES;
            [self.channel closeChannel];
            return;
        }
        offset += length;
    }
    [self.standardInput readInBackgroundAndNotify];
}

- (void)rfcommChannelData:(IOBluetoothRFCOMMChannel *)channel
                     data:(void *)dataPointer
                   length:(size_t)dataLength {
    const uint8_t *bytes = dataPointer;
    size_t offset = 0;
    while (offset < dataLength) {
        ssize_t count = write(STDOUT_FILENO, bytes + offset, dataLength - offset);
        if (count <= 0) {
            self.finished = YES;
            [channel closeChannel];
            return;
        }
        offset += (size_t)count;
    }
}

- (void)rfcommChannelClosed:(IOBluetoothRFCOMMChannel *)channel {
    fprintf(stderr, "CLOSED\n");
    fflush(stderr);
    self.finished = YES;
}

@end

static IOBluetoothDevice *FindPairedDevice(NSString *selector) {
    NSString *normalized = [selector stringByReplacingOccurrencesOfString:@":" withString:@"-"];
    if (normalized.length == 17) {
        IOBluetoothDevice *byAddress = [IOBluetoothDevice deviceWithAddressString:normalized];
        if (byAddress != nil && byAddress.isPaired) {
            return byAddress;
        }
    }
    for (IOBluetoothDevice *candidate in [IOBluetoothDevice pairedDevices]) {
        if ([candidate.name caseInsensitiveCompare:selector] == NSOrderedSame) {
            return candidate;
        }
    }
    return nil;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSString *deviceSelector = argc > 1
            ? [NSString stringWithUTF8String:argv[1]]
            : @"ExpandableEEGBT";
        BluetoothRFCOMMChannelID channelID = (BluetoothRFCOMMChannelID)(
            argc > 2 ? MAX(1, MIN(30, atoi(argv[2]))) : 1
        );
        IOBluetoothDevice *device = FindPairedDevice(deviceSelector);
        if (device == nil) {
            fprintf(stderr, "ERROR device_not_paired selector=%s\n", deviceSelector.UTF8String);
            return 2;
        }

        RFCOMMBridge *bridge = [[RFCOMMBridge alloc] init];
        IOBluetoothRFCOMMChannel *channel = nil;
        IOReturn openStatus = [device openRFCOMMChannelSync:&channel
                                             withChannelID:channelID
                                                  delegate:bridge];
        if (openStatus != kIOReturnSuccess || channel == nil || !channel.isOpen) {
            fprintf(stderr,
                    "ERROR rfcomm_open_failed status=0x%08x channel=%u device=%s\n",
                    openStatus, channelID, device.name.UTF8String ?: "(unknown)");
            return 3;
        }
        bridge.channel = channel;
        IOReturn serialStatus = [channel setSerialParameters:115200
                                                   dataBits:8
                                                     parity:kBluetoothRFCOMMParityTypeNoParity
                                                   stopBits:1];
        if (serialStatus != kIOReturnSuccess) {
            fprintf(stderr, "ERROR serial_config_failed status=0x%08x\n", serialStatus);
            [channel closeChannel];
            return 4;
        }

        fprintf(stderr,
                "READY device=%s address=%s channel=%u mtu=%u\n",
                device.name.UTF8String ?: "(unknown)",
                device.addressString.UTF8String ?: "(unknown)",
                channelID,
                channel.getMTU);
        fflush(stderr);
        [bridge startReadingCommands];
        while (!bridge.finished && channel.isOpen) {
            @autoreleasepool {
                NSDate *slice = [NSDate dateWithTimeIntervalSinceNow:0.05];
                [[NSRunLoop currentRunLoop] runMode:NSDefaultRunLoopMode beforeDate:slice];
            }
        }
        [[NSNotificationCenter defaultCenter] removeObserver:bridge];
        if (channel.isOpen) {
            [channel closeChannel];
        }
        return 0;
    }
}
