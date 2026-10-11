#import <ApplicationServices/ApplicationServices.h>
#import <Foundation/Foundation.h>

static BOOL EmitJSON(NSDictionary *value, NSError **error) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:value
                                                   options:NSJSONWritingPrettyPrinted | NSJSONWritingSortedKeys
                                                     error:error];
    if (!data) return NO;
    [[NSFileHandle fileHandleWithStandardOutput] writeData:data];
    [[NSFileHandle fileHandleWithStandardOutput]
        writeData:[@"\n" dataUsingEncoding:NSUTF8StringEncoding]];
    return YES;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSArray<NSString *> *arguments =
            [[NSProcessInfo processInfo].arguments subarrayWithRange:NSMakeRange(1, MAX(0, argc - 1))];
        if (arguments.count > 0 && ![arguments isEqualToArray:@[@"--self-test"]]) {
            fprintf(stderr, "usage: chat-accessibility-poc [--self-test]\n");
            return 2;
        }

        NSDictionary *options = @{
            (__bridge NSString *)kAXTrustedCheckOptionPrompt: @NO,
        };
        BOOL trusted = AXIsProcessTrusted();
        BOOL trustedWithOptions =
            AXIsProcessTrustedWithOptions((__bridge CFDictionaryRef)options);
        NSDictionary *report = @{
            @"schemaVersion": @1,
            @"mode": @"accessibility_trust_self_test",
            @"accessibilityTrusted": @(trusted),
            @"accessibilityTrustedWithOptions": @(trustedWithOptions),
            @"contentRead": @NO,
            @"safety": @{
                @"accessibilityPromptRequested": @NO,
                @"clientContentInspected": @NO,
                @"uiMutationPerformed": @NO,
            },
        };

        NSError *error = nil;
        if (!EmitJSON(report, &error)) {
            fprintf(stderr, "accessibility_poc_failed: %s\n",
                    error.localizedDescription.UTF8String);
            return 1;
        }
        return 0;
    }
}
