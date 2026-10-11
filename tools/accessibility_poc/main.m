#import <AppKit/AppKit.h>
#import <ApplicationServices/ApplicationServices.h>

static const NSUInteger MaximumNodes = 5000;
static const NSUInteger MaximumDepth = 14;
static const NSUInteger MaximumAttributeCharacters = 4096;
static const NSUInteger MaximumObservedCharacters = 1024 * 1024;

static NSArray<NSDictionary *> *TargetSpecs(void) {
    return @[
        @{ @"key": @"qq", @"bundleIdentifiers": [NSSet setWithArray:@[@"com.tencent.qq"]],
           @"paths": @[@"/Applications/QQ.app"] },
        @{ @"key": @"wechat", @"bundleIdentifiers": [NSSet setWithArray:@[@"com.tencent.xinWeChat"]],
           @"paths": @[@"/Applications/WeChat.app", @"/Applications/微信.app"] },
    ];
}

static NSRunningApplication *RunningApplication(NSDictionary *target) {
    for (NSRunningApplication *application in NSWorkspace.sharedWorkspace.runningApplications) {
        NSString *bundle = application.bundleIdentifier ?: @"";
        if ([target[@"bundleIdentifiers"] containsObject:bundle]) return application;
    }
    return nil;
}

static NSDictionary *InstalledMetadata(NSDictionary *target) {
    for (NSString *path in target[@"paths"]) {
        if (![NSFileManager.defaultManager fileExistsAtPath:path]) continue;
        NSBundle *bundle = [NSBundle bundleWithPath:path];
        return @{ @"installed": @YES,
                  @"version": [bundle objectForInfoDictionaryKey:@"CFBundleShortVersionString"] ?: [NSNull null] };
    }
    return @{ @"installed": @NO, @"version": [NSNull null] };
}

static NSDictionary *ProcessReport(NSDictionary *target) {
    NSRunningApplication *application = RunningApplication(target);
    NSDictionary *installed = InstalledMetadata(target);
    NSString *version = application.bundleURL
        ? [[NSBundle bundleWithURL:application.bundleURL] objectForInfoDictionaryKey:@"CFBundleShortVersionString"]
        : nil;
    return @{
        @"target": target[@"key"], @"installed": installed[@"installed"],
        @"version": version ?: installed[@"version"], @"running": @(application != nil),
        @"bundleIdentifier": application.bundleIdentifier ?: [NSNull null],
        @"inspected": @NO,
        @"status": application ? @"detected_not_inspected" : @"not_running",
    };
}

static CFTypeRef CopyAttribute(AXUIElementRef element, CFStringRef attribute) {
    CFTypeRef value = NULL;
    return AXUIElementCopyAttributeValue(element, attribute, &value) == kAXErrorSuccess ? value : NULL;
}

static NSString *RoleAttribute(AXUIElementRef element) {
    // AXRole is an operating-system vocabulary value, not application content.
    CFTypeRef value = CopyAttribute(element, kAXRoleAttribute);
    if (!value) return nil;
    NSString *result = CFGetTypeID(value) == CFStringGetTypeID() ? [(__bridge NSString *)value copy] : nil;
    CFRelease(value);
    return result.length <= 128 ? result : nil;
}

static NSString *BoundedVisibleString(AXUIElementRef element,
                                      NSUInteger maximumCharacters,
                                      BOOL *truncated) {
    CFTypeRef visibleValue = CopyAttribute(element, kAXVisibleCharacterRangeAttribute);
    if (!visibleValue || CFGetTypeID(visibleValue) != AXValueGetTypeID()
        || AXValueGetType((AXValueRef)visibleValue) != kAXValueCFRangeType) {
        if (visibleValue) CFRelease(visibleValue);
        return nil;
    }
    CFRange visibleRange = CFRangeMake(0, 0);
    BOOL valid = AXValueGetValue(
        (AXValueRef)visibleValue, kAXValueCFRangeType, &visibleRange
    );
    CFRelease(visibleValue);
    if (!valid || visibleRange.location < 0 || visibleRange.length <= 0
        || maximumCharacters == 0) return nil;
    CFRange requested = visibleRange;
    if ((NSUInteger)requested.length > maximumCharacters) {
        requested.length = (CFIndex)maximumCharacters;
        if (truncated) *truncated = YES;
    }
    AXValueRef rangeValue = AXValueCreate(kAXValueCFRangeType, &requested);
    if (!rangeValue) return nil;
    CFTypeRef stringValue = NULL;
    AXError status = AXUIElementCopyParameterizedAttributeValue(
        element, kAXStringForRangeParameterizedAttribute, rangeValue, &stringValue
    );
    CFRelease(rangeValue);
    if (status != kAXErrorSuccess || !stringValue
        || CFGetTypeID(stringValue) != CFStringGetTypeID()) {
        if (stringValue) CFRelease(stringValue);
        return nil;
    }
    NSString *result = [(__bridge NSString *)stringValue copy];
    CFRelease(stringValue);
    if (result.length > maximumCharacters) return nil;
    return result;
}

static NSArray *ChildElements(AXUIElementRef element, NSUInteger maximumCount) {
    if (maximumCount == 0) return @[];
    CFIndex available = 0;
    if (AXUIElementGetAttributeValueCount(element, kAXChildrenAttribute, &available) != kAXErrorSuccess
        || available <= 0) return @[];
    CFArrayRef values = NULL;
    CFIndex requested = MIN(available, (CFIndex)maximumCount);
    if (AXUIElementCopyAttributeValues(
            element, kAXChildrenAttribute, 0, requested, &values
        ) != kAXErrorSuccess || !values) return @[];
    return CFBridgingRelease(values);
}

static BOOL ElementFrame(AXUIElementRef element, CGRect *frame) {
    CFTypeRef positionValue = CopyAttribute(element, kAXPositionAttribute);
    CFTypeRef sizeValue = CopyAttribute(element, kAXSizeAttribute);
    CGPoint position = CGPointZero;
    CGSize size = CGSizeZero;
    BOOL valid = positionValue && sizeValue
        && CFGetTypeID(positionValue) == AXValueGetTypeID()
        && CFGetTypeID(sizeValue) == AXValueGetTypeID()
        && AXValueGetType((AXValueRef)positionValue) == kAXValueCGPointType
        && AXValueGetType((AXValueRef)sizeValue) == kAXValueCGSizeType
        && AXValueGetValue((AXValueRef)positionValue, kAXValueCGPointType, &position)
        && AXValueGetValue((AXValueRef)sizeValue, kAXValueCGSizeType, &size)
        && size.width > 0 && size.height > 0;
    if (positionValue) CFRelease(positionValue);
    if (sizeValue) CFRelease(sizeValue);
    if (valid) *frame = (CGRect){position, size};
    return valid;
}

static NSArray<NSDictionary *> *Observe(AXUIElementRef root) {
    NSMutableArray<NSDictionary *> *result = [NSMutableArray array];
    NSMutableArray<NSDictionary *> *queue = [NSMutableArray arrayWithObject:@{
        @"element": (__bridge id)root, @"depth": @0,
    }];
    CGRect visibleWindowFrame = CGRectZero;
    BOOL hasVisibleWindowFrame = ElementFrame(root, &visibleWindowFrame);
    NSUInteger observedCharacters = 0;
    NSUInteger truncatedAttributeCount = 0;
    while (queue.count && result.count < MaximumNodes) {
        NSDictionary *queued = queue.firstObject;
        [queue removeObjectAtIndex:0];
        AXUIElementRef element = (__bridge AXUIElementRef)queued[@"element"];
        NSUInteger depth = [queued[@"depth"] unsignedIntegerValue];
        NSUInteger occupiedAfterCurrent = result.count + queue.count + 1;
        NSUInteger remainingNodes = occupiedAfterCurrent < MaximumNodes
            ? MaximumNodes - occupiedAfterCurrent : 0;
        NSArray *children = depth < MaximumDepth ? ChildElements(element, remainingNodes) : @[];
        NSString *role = RoleAttribute(element);
        NSMutableArray<NSString *> *strings = [NSMutableArray array];
        CGRect elementFrame = CGRectZero;
        BOOL isVisible = hasVisibleWindowFrame && ElementFrame(element, &elementFrame)
            && CGRectIntersectsRect(visibleWindowFrame, elementFrame);
        if (isVisible && [role isEqualToString:(__bridge NSString *)kAXStaticTextRole]) {
            NSUInteger remainingCharacters = MaximumObservedCharacters - observedCharacters;
            BOOL truncated = NO;
            NSString *value = BoundedVisibleString(
                element, MIN(MaximumAttributeCharacters, remainingCharacters), &truncated
            );
            if (value && [value stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet].length) {
                [strings addObject:value];
                observedCharacters += value.length;
            }
            if (truncated) truncatedAttributeCount++;
        }
        NSString *signature = [NSString stringWithFormat:@"%lu|%@|%@|%lu",
            (unsigned long)result.count, role ?: @"", @"", (unsigned long)children.count];
        [result addObject:@{ @"order": @(result.count), @"role": role ?: [NSNull null],
            @"identifier": [NSNull null], @"strings": strings,
            @"childCount": @(children.count), @"signature": signature,
            @"observedCharacters": @(observedCharacters),
            @"truncatedAttributeCount": @(truncatedAttributeCount) }];
        for (id child in children) {
            [queue addObject:@{ @"element": child, @"depth": @(depth + 1) }];
        }
    }
    return result;
}

static NSDictionary *ClassifyObservations(NSArray<NSDictionary *> *first,
                                           NSArray<NSDictionary *> *second) {
    NSSet *textRoles = [NSSet setWithArray:@[(__bridge NSString *)kAXStaticTextRole]];
    NSMutableArray<NSDictionary *> *candidates = [NSMutableArray array];
    NSMutableArray<NSString *> *allStrings = [NSMutableArray array];
    NSMutableDictionary<NSString *, NSNumber *> *roles = [NSMutableDictionary dictionary];
    NSMutableArray<NSString *> *identifiers = [NSMutableArray array];
    for (NSDictionary *item in first) {
        NSString *role = item[@"role"] == [NSNull null] ? nil : item[@"role"];
        NSString *identifier = item[@"identifier"] == [NSNull null] ? nil : item[@"identifier"];
        if (role) roles[role] = @([roles[role] integerValue] + 1);
        if (identifier.length) [identifiers addObject:identifier];
        if (role && [textRoles containsObject:role] && [item[@"strings"] count]) {
            [candidates addObject:item]; [allStrings addObjectsFromArray:item[@"strings"]];
        }
    }
    NSRegularExpression *timePattern = [NSRegularExpression regularExpressionWithPattern:
        @"(?:^|\\s)(?:[01]?\\d|2[0-3]):[0-5]\\d(?:\\s|$)" options:0 error:nil];
    NSUInteger timeCount = 0;
    for (NSString *value in allStrings) {
        if ([timePattern firstMatchInString:value options:0 range:NSMakeRange(0, value.length)]) timeCount++;
    }
    NSArray *firstSignatures = [first valueForKey:@"signature"];
    NSArray *secondSignatures = [second valueForKey:@"signature"];
    NSString *identifierStatus = identifiers.count == 0 ? @"NO" :
        [NSSet setWithArray:identifiers].count == identifiers.count ? @"YES" : @"PARTIAL";
    BOOL hasCandidates = candidates.count > 0;
    NSNumber *observedCharacters = first.lastObject[@"observedCharacters"] ?: @0;
    NSNumber *truncatedAttributeCount = first.lastObject[@"truncatedAttributeCount"] ?: @0;
    return @{
        @"nodeCount": @(first.count), @"roles": roles,
        @"observedCharacterCount": observedCharacters,
        @"truncatedAttributeCount": truncatedAttributeCount,
        @"textCandidateCount": @(candidates.count), @"timeLabelCandidateCount": @(timeCount),
        @"sender": hasCandidates ? @"UNVERIFIED" : @"NO",
        @"timestamp": timeCount ? @"UNVERIFIED" : @"NO",
        @"messageText": hasCandidates ? @"UNVERIFIED" : @"NO",
        @"stableOrder": hasCandidates ? @"UNVERIFIED" : @"NO",
        @"treeShapeStable": first.count && [firstSignatures isEqualToArray:secondSignatures] ? @"YES" : @"NO",
        @"uniqueIdentifiers": identifierStatus, @"virtualizedList": @"UNKNOWN_NO_SCROLL_PERFORMED",
        @"selfOtherDistinction": @"UNKNOWN",
        @"status": hasCandidates
            ? @"HUMAN_REVIEW_REQUIRED_TEXT_CANDIDATES_ARE_NOT_MESSAGES"
            : @"NO_GO_NO_ACCESSIBLE_TEXT_CANDIDATES",
    };
}

static NSDictionary *Inspect(NSDictionary *target, NSError **error) {
    if (!AXIsProcessTrusted()) {
        *error = [NSError errorWithDomain:@"AccessibilityPOC" code:2
            userInfo:@{NSLocalizedDescriptionKey: @"accessibility_not_authorized"}];
        return nil;
    }
    NSRunningApplication *application = RunningApplication(target);
    if (!application) {
        *error = [NSError errorWithDomain:@"AccessibilityPOC" code:3
            userInfo:@{NSLocalizedDescriptionKey: @"target_not_running"}];
        return nil;
    }
    AXUIElementRef applicationElement = AXUIElementCreateApplication(application.processIdentifier);
    CFTypeRef focusedValue = CopyAttribute(applicationElement, kAXFocusedWindowAttribute);
    CFRelease(applicationElement);
    if (!focusedValue || CFGetTypeID(focusedValue) != AXUIElementGetTypeID()) {
        if (focusedValue) CFRelease(focusedValue);
        *error = [NSError errorWithDomain:@"AccessibilityPOC" code:4
            userInfo:@{NSLocalizedDescriptionKey: @"focused_window_unavailable"}];
        return nil;
    }
    AXUIElementRef window = (AXUIElementRef)focusedValue;
    NSArray<NSDictionary *> *first = Observe(window);
    NSArray<NSDictionary *> *second = Observe(window);
    CFRelease(window);
    NSString *version = application.bundleURL
        ? [[NSBundle bundleWithURL:application.bundleURL] objectForInfoDictionaryKey:@"CFBundleShortVersionString"] : nil;
    NSMutableDictionary *report = [ClassifyObservations(first, second) mutableCopy];
    [report addEntriesFromDictionary:@{
        @"schemaVersion": @1, @"mode": @"visible_window_read_only", @"target": target[@"key"],
        @"applicationVersion": version ?: [NSNull null], @"accessibilityTrusted": @YES,
    }];
    return report;
}

static BOOL EmitJSON(id value, NSError **error) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:value options:NSJSONWritingPrettyPrinted | NSJSONWritingSortedKeys error:error];
    if (!data) return NO;
    [[NSFileHandle fileHandleWithStandardOutput] writeData:data];
    [[NSFileHandle fileHandleWithStandardOutput] writeData:[@"\n" dataUsingEncoding:NSUTF8StringEncoding]];
    return YES;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSArray<NSString *> *arguments = [[NSProcessInfo processInfo].arguments subarrayWithRange:NSMakeRange(1, MAX(0, argc - 1))];
        NSError *error = nil;
        if ([arguments isEqualToArray:@[@"--classifier-self-test"]]) {
            NSArray *containerOnly = @[@{ @"role": @"AXGroup", @"identifier": [NSNull null],
                @"strings": @[], @"signature": @"0|AXGroup||0" }];
            NSArray *windowLabels = @[@{ @"role": @"AXStaticText", @"identifier": @"toolbar-title",
                @"strings": @[@"Settings 10:20"], @"signature": @"0|AXStaticText|toolbar-title|0" }];
            NSDictionary *report = @{
                @"containerOnly": ClassifyObservations(containerOnly, containerOnly),
                @"unclassifiedWindowLabel": ClassifyObservations(windowLabels, windowLabels),
            };
            if (!EmitJSON(report, &error)) {
                fprintf(stderr, "accessibility_poc_failed: %s\n", error.localizedDescription.UTF8String);
                return 1;
            }
            return 0;
        }
        if ([arguments isEqualToArray:@[@"--self-test"]]) {
            NSMutableArray *processes = [NSMutableArray array];
            for (NSDictionary *target in TargetSpecs()) [processes addObject:ProcessReport(target)];
            NSDictionary *report = @{ @"schemaVersion": @1, @"mode": @"self_test",
                @"accessibilityTrusted": @(AXIsProcessTrusted()), @"contentRead": @NO,
                @"processes": processes, @"safety": @{ @"accessibilityPromptRequested": @NO,
                    @"clientContentInspected": @NO, @"scrollPerformed": @NO, @"uiMutationPerformed": @NO } };
            if (!EmitJSON(report, &error)) {
                fprintf(stderr, "accessibility_poc_failed: %s\n", error.localizedDescription.UTF8String);
                return 1;
            }
            return 0;
        }
        NSUInteger targetIndex = [arguments indexOfObject:@"--target"];
        NSString *targetKey = targetIndex != NSNotFound && targetIndex + 1 < arguments.count ? arguments[targetIndex + 1] : nil;
        NSDictionary *target = nil;
        for (NSDictionary *candidate in TargetSpecs()) if ([candidate[@"key"] isEqual:targetKey]) target = candidate;
        if (!target || ![arguments containsObject:@"--acknowledge-visible-content"]) {
            error = [NSError errorWithDomain:@"AccessibilityPOC" code:1 userInfo:@{NSLocalizedDescriptionKey:
                @"use --self-test, or --target qq|wechat --acknowledge-visible-content"}];
            fprintf(stderr, "accessibility_poc_failed: %s\n", error.localizedDescription.UTF8String);
            return 1;
        }
        NSDictionary *report = Inspect(target, &error);
        if (!report || !EmitJSON(report, &error)) {
            fprintf(stderr, "accessibility_poc_failed: %s\n", error.localizedDescription.UTF8String);
            return 1;
        }
        return 0;
    }
}
