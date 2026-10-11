#import <AppKit/AppKit.h>
#import <ApplicationServices/ApplicationServices.h>
#import <float.h>
#import <math.h>
#import <unistd.h>

static const NSUInteger MaximumNodes = 5000;
static const NSUInteger MaximumDepth = 14;
static const NSUInteger MaximumAttributeCharacters = 1024;
static const NSUInteger MaximumObservedCharacters = 128 * 1024;
static const NSUInteger RequiredMarkerCount = 3;
static const NSUInteger RequiredReadCount = 3;

static NSArray<NSDictionary *> *TargetSpecs(void) {
    return @[
        @{ @"key": @"qq", @"bundleIdentifier": @"com.tencent.qq",
           @"paths": @[@"/Applications/QQ.app"] },
        @{ @"key": @"wechat", @"bundleIdentifier": @"com.tencent.xinWeChat",
           @"paths": @[@"/Applications/WeChat.app", @"/Applications/微信.app"] },
    ];
}

static NSDictionary *TargetForKey(NSString *key) {
    for (NSDictionary *target in TargetSpecs()) {
        if ([target[@"key"] isEqualToString:key]) return target;
    }
    return nil;
}

static NSRunningApplication *RunningApplication(NSDictionary *target) {
    NSString *expectedBundleIdentifier = target[@"bundleIdentifier"];
    for (NSRunningApplication *application in NSWorkspace.sharedWorkspace.runningApplications) {
        if ([application.bundleIdentifier isEqualToString:expectedBundleIdentifier]) return application;
    }
    return nil;
}

static NSDictionary *ProcessReport(NSDictionary *target) {
    NSRunningApplication *application = RunningApplication(target);
    NSString *version = nil;
    if (application.bundleURL) {
        version = [[NSBundle bundleWithURL:application.bundleURL]
            objectForInfoDictionaryKey:@"CFBundleShortVersionString"];
    }
    if (!version) {
        for (NSString *path in target[@"paths"]) {
            if (![NSFileManager.defaultManager fileExistsAtPath:path]) continue;
            version = [[NSBundle bundleWithPath:path]
                objectForInfoDictionaryKey:@"CFBundleShortVersionString"];
            break;
        }
    }
    return @{
        @"target": target[@"key"],
        @"running": @(application != nil),
        @"version": version ?: [NSNull null],
        @"contentRead": @NO,
    };
}

static CFTypeRef CopyAttribute(AXUIElementRef element, CFStringRef attribute) {
    CFTypeRef value = NULL;
    AXError status = AXUIElementCopyAttributeValue(element, attribute, &value);
    return status == kAXErrorSuccess ? value : NULL;
}

static NSString *CopyBoundedStringAttribute(AXUIElementRef element,
                                             CFStringRef attribute,
                                             NSUInteger maximumCharacters,
                                             BOOL *truncated) {
    if (maximumCharacters == 0) return nil;
    CFTypeRef value = CopyAttribute(element, attribute);
    if (!value) return nil;
    NSString *result = nil;
    if (CFGetTypeID(value) == CFStringGetTypeID()) {
        NSString *string = (__bridge NSString *)value;
        if (string.length > maximumCharacters) {
            result = [string substringToIndex:maximumCharacters];
            if (truncated) *truncated = YES;
        } else {
            result = [string copy];
        }
    }
    CFRelease(value);
    return result;
}

static NSString *CopyVisibleString(AXUIElementRef element,
                                    NSUInteger maximumCharacters,
                                    BOOL *truncated) {
    if (maximumCharacters == 0) return nil;
    CFTypeRef visibleValue = CopyAttribute(element, kAXVisibleCharacterRangeAttribute);
    if (!visibleValue || CFGetTypeID(visibleValue) != AXValueGetTypeID()
        || AXValueGetType((AXValueRef)visibleValue) != kAXValueCFRangeType) {
        if (visibleValue) CFRelease(visibleValue);
        return nil;
    }
    CFRange visibleRange = CFRangeMake(0, 0);
    BOOL valid = AXValueGetValue((AXValueRef)visibleValue, kAXValueCFRangeType,
                                 &visibleRange);
    CFRelease(visibleValue);
    if (!valid || visibleRange.location < 0 || visibleRange.length <= 0) return nil;

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
    if (result.length > maximumCharacters) {
        if (truncated) *truncated = YES;
        return [result substringToIndex:maximumCharacters];
    }
    return result;
}

static NSString *RoleAttribute(AXUIElementRef element) {
    return CopyBoundedStringAttribute(element, kAXRoleAttribute, 128, NULL);
}

static NSString *SubroleAttribute(AXUIElementRef element) {
    return CopyBoundedStringAttribute(element, kAXSubroleAttribute, 128, NULL);
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
    if (valid && frame) *frame = (CGRect){position, size};
    return valid;
}

static NSArray *ChildElements(AXUIElementRef element, NSUInteger maximumCount) {
    if (maximumCount == 0) return @[];
    CFIndex available = 0;
    if (AXUIElementGetAttributeValueCount(element, kAXChildrenAttribute, &available)
            != kAXErrorSuccess || available <= 0) return @[];
    CFArrayRef values = NULL;
    CFIndex requested = MIN(available, (CFIndex)maximumCount);
    if (AXUIElementCopyAttributeValues(element, kAXChildrenAttribute, 0, requested,
                                       &values) != kAXErrorSuccess || !values) {
        return @[];
    }
    return CFBridgingRelease(values);
}

static BOOL IsTextRole(NSString *role) {
    static NSSet<NSString *> *roles;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        roles = [NSSet setWithArray:@[@"AXStaticText", @"AXTextArea", @"AXTextField"]];
    });
    return [roles containsObject:role];
}

static void AddUniqueString(NSMutableArray<NSString *> *strings,
                            NSString *value,
                            NSUInteger *observedCharacters) {
    if (!value.length) return;
    NSString *trimmed = [value stringByTrimmingCharactersInSet:
        NSCharacterSet.whitespaceAndNewlineCharacterSet];
    if (!trimmed.length || [strings containsObject:trimmed]) return;
    [strings addObject:trimmed];
    *observedCharacters += trimmed.length;
}

static NSArray<NSDictionary *> *Observe(AXUIElementRef root,
                                         CGRect windowFrame,
                                         BOOL *boundsExceeded) {
    NSMutableArray<NSDictionary *> *result = [NSMutableArray array];
    NSMutableArray<NSDictionary *> *queue = [NSMutableArray arrayWithObject:@{
        @"element": (__bridge id)root,
        @"depth": @0,
        @"parentOrder": @(-1),
    }];
    CGRect contentRegion = CGRectMake(
        CGRectGetMinX(windowFrame) + CGRectGetWidth(windowFrame) * 0.22,
        CGRectGetMinY(windowFrame),
        CGRectGetWidth(windowFrame) * 0.78,
        CGRectGetHeight(windowFrame)
    );
    NSUInteger observedCharacters = 0;
    NSUInteger truncatedAttributeCount = 0;

    while (queue.count && result.count < MaximumNodes) {
        NSDictionary *queued = queue.firstObject;
        [queue removeObjectAtIndex:0];
        AXUIElementRef element = (__bridge AXUIElementRef)queued[@"element"];
        NSUInteger depth = [queued[@"depth"] unsignedIntegerValue];
        NSInteger order = (NSInteger)result.count;
        NSUInteger occupiedAfterCurrent = result.count + queue.count + 1;
        NSUInteger remainingNodes = occupiedAfterCurrent < MaximumNodes
            ? MaximumNodes - occupiedAfterCurrent : 0;
        NSArray *children = depth < MaximumDepth
            ? ChildElements(element, remainingNodes) : @[];
        NSString *role = RoleAttribute(element);
        NSString *subrole = SubroleAttribute(element);
        CGRect frame = CGRectZero;
        BOOL hasFrame = ElementFrame(element, &frame);
        BOOL inContentRegion = hasFrame
            && CGRectIntersectsRect(windowFrame, frame)
            && CGRectIntersectsRect(contentRegion, frame);
        NSMutableArray<NSString *> *strings = [NSMutableArray array];

        if (inContentRegion && IsTextRole(role)
            && observedCharacters < MaximumObservedCharacters) {
            NSUInteger remainingCharacters = MaximumObservedCharacters - observedCharacters;
            NSUInteger perAttributeLimit = MIN(MaximumAttributeCharacters,
                                               remainingCharacters);
            BOOL truncated = NO;
            AddUniqueString(strings,
                CopyVisibleString(element, perAttributeLimit, &truncated),
                &observedCharacters);
            for (id attribute in @[
                    (__bridge NSString *)kAXValueAttribute,
                    (__bridge NSString *)kAXTitleAttribute,
                    (__bridge NSString *)kAXDescriptionAttribute]) {
                if (observedCharacters >= MaximumObservedCharacters) break;
                remainingCharacters = MaximumObservedCharacters - observedCharacters;
                perAttributeLimit = MIN(MaximumAttributeCharacters, remainingCharacters);
                AddUniqueString(strings,
                    CopyBoundedStringAttribute(element,
                        (__bridge CFStringRef)attribute,
                        perAttributeLimit, &truncated),
                    &observedCharacters);
            }
            if (truncated) truncatedAttributeCount++;
        }

        [result addObject:@{
            @"order": @(order),
            @"parentOrder": queued[@"parentOrder"],
            @"role": role ?: [NSNull null],
            @"subrole": subrole ?: [NSNull null],
            @"strings": strings,
            @"childCount": @(children.count),
            @"hasFrame": @(hasFrame),
            @"x": @(frame.origin.x),
            @"y": @(frame.origin.y),
            @"width": @(frame.size.width),
            @"height": @(frame.size.height),
            @"truncatedAttributeCount": @(truncatedAttributeCount),
        }];

        for (id child in children) {
            [queue addObject:@{
                @"element": child,
                @"depth": @(depth + 1),
                @"parentOrder": @(order),
            }];
        }
    }
    if (boundsExceeded) {
        *boundsExceeded = queue.count > 0
            || observedCharacters >= MaximumObservedCharacters;
    }
    return result;
}

static BOOL ItemContainsMarker(NSDictionary *item, NSString *marker) {
    for (NSString *value in item[@"strings"]) {
        if ([value rangeOfString:marker options:NSLiteralSearch].location != NSNotFound) {
            return YES;
        }
    }
    return NO;
}

static NSArray<NSDictionary *> *MarkerMatches(NSArray<NSDictionary *> *observations,
                                               NSArray<NSString *> *markers) {
    NSMutableArray<NSDictionary *> *matches = [NSMutableArray array];
    [markers enumerateObjectsUsingBlock:^(NSString *marker, NSUInteger markerIndex,
                                          BOOL *stop) {
        NSMutableArray<NSDictionary *> *occurrences = [NSMutableArray array];
        for (NSDictionary *item in observations) {
            if (ItemContainsMarker(item, marker)) [occurrences addObject:item];
        }
        NSDictionary *selected = occurrences.firstObject;
        [matches addObject:@{
            @"markerIndex": @(markerIndex),
            @"occurrenceCount": @(occurrences.count),
            @"selected": selected ?: [NSNull null],
        }];
    }];
    return matches;
}

static NSArray<NSNumber *> *OrderedMarkerIndexes(NSArray<NSDictionary *> *matches,
                                                  BOOL visualOrder) {
    NSMutableArray<NSDictionary *> *found = [NSMutableArray array];
    for (NSDictionary *match in matches) {
        if (match[@"selected"] != [NSNull null]) [found addObject:match];
    }
    [found sortUsingComparator:^NSComparisonResult(NSDictionary *left,
                                                    NSDictionary *right) {
        NSDictionary *leftItem = left[@"selected"];
        NSDictionary *rightItem = right[@"selected"];
        double leftPrimary = visualOrder ? [leftItem[@"y"] doubleValue]
                                         : [leftItem[@"order"] doubleValue];
        double rightPrimary = visualOrder ? [rightItem[@"y"] doubleValue]
                                          : [rightItem[@"order"] doubleValue];
        if (leftPrimary < rightPrimary) return NSOrderedAscending;
        if (leftPrimary > rightPrimary) return NSOrderedDescending;
        double leftSecondary = [leftItem[@"x"] doubleValue];
        double rightSecondary = [rightItem[@"x"] doubleValue];
        if (leftSecondary < rightSecondary) return NSOrderedAscending;
        if (leftSecondary > rightSecondary) return NSOrderedDescending;
        return NSOrderedSame;
    }];
    return [found valueForKey:@"markerIndex"];
}

static BOOL MatchesAreCompleteAndUnique(NSArray<NSDictionary *> *matches) {
    NSMutableSet<NSNumber *> *orders = [NSMutableSet set];
    for (NSDictionary *match in matches) {
        if ([match[@"occurrenceCount"] unsignedIntegerValue] != 1) return NO;
        NSDictionary *item = match[@"selected"];
        if (item == (id)[NSNull null]) return NO;
        [orders addObject:item[@"order"]];
    }
    return orders.count == matches.count;
}

static BOOL HasSenderAlignmentEvidence(NSArray<NSDictionary *> *matches,
                                       double windowWidth) {
    if (!MatchesAreCompleteAndUnique(matches)) return NO;
    double minimumX = DBL_MAX;
    double maximumX = -DBL_MAX;
    for (NSDictionary *match in matches) {
        NSDictionary *item = match[@"selected"];
        double centerX = [item[@"x"] doubleValue] + [item[@"width"] doubleValue] / 2.0;
        minimumX = MIN(minimumX, centerX);
        maximumX = MAX(maximumX, centerX);
    }
    return maximumX - minimumX >= MAX(40.0, windowWidth * 0.18);
}

static BOOL StringLooksLikeTime(NSString *value) {
    static NSRegularExpression *expression;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        expression = [NSRegularExpression regularExpressionWithPattern:
            @"(?:^|\\s)(?:[01]?\\d|2[0-3]):[0-5]\\d(?:\\s|$)"
            options:0 error:nil];
    });
    return [expression firstMatchInString:value options:0
                                     range:NSMakeRange(0, value.length)] != nil;
}

static BOOL HasNearbyTimeEvidence(NSArray<NSDictionary *> *observations,
                                  NSArray<NSDictionary *> *matches) {
    NSMutableArray<NSDictionary *> *timeItems = [NSMutableArray array];
    for (NSDictionary *item in observations) {
        BOOL isTime = NO;
        for (NSString *value in item[@"strings"]) {
            if (StringLooksLikeTime(value)) { isTime = YES; break; }
        }
        if (isTime) [timeItems addObject:item];
    }
    if (!timeItems.count) return NO;
    for (NSDictionary *match in matches) {
        NSDictionary *markerItem = match[@"selected"];
        if (markerItem == (id)[NSNull null]) continue;
        double markerY = [markerItem[@"y"] doubleValue];
        for (NSDictionary *timeItem in timeItems) {
            if (fabs([timeItem[@"y"] doubleValue] - markerY) <= 120.0) return YES;
        }
    }
    return NO;
}

static NSDictionary *ClassifyReads(NSArray<NSArray<NSDictionary *> *> *reads,
                                    NSArray<NSString *> *markers,
                                    double windowWidth,
                                    BOOL boundsExceeded) {
    NSArray<NSNumber *> *expectedOrder = @[@0, @1, @2];
    NSMutableArray<NSArray<NSDictionary *> *> *allMatches = [NSMutableArray array];
    BOOL allReadsComplete = reads.count == RequiredReadCount;
    BOOL anyMarkerFound = NO;
    BOOL allOrderEvidence = allReadsComplete;
    BOOL allSenderEvidence = allReadsComplete;
    BOOL allTimestampEvidence = allReadsComplete;

    for (NSArray<NSDictionary *> *read in reads) {
        NSArray<NSDictionary *> *matches = MarkerMatches(read, markers);
        [allMatches addObject:matches];
        NSUInteger foundCount = 0;
        for (NSDictionary *match in matches) {
            if ([match[@"occurrenceCount"] unsignedIntegerValue] > 0) foundCount++;
        }
        anyMarkerFound = anyMarkerFound || foundCount > 0;
        BOOL completeAndUnique = MatchesAreCompleteAndUnique(matches);
        allReadsComplete = allReadsComplete && foundCount == markers.count;
        allOrderEvidence = allOrderEvidence && completeAndUnique
            && [OrderedMarkerIndexes(matches, YES) isEqualToArray:expectedOrder]
            && [OrderedMarkerIndexes(matches, NO) isEqualToArray:expectedOrder];
        allSenderEvidence = allSenderEvidence
            && HasSenderAlignmentEvidence(matches, windowWidth);
        allTimestampEvidence = allTimestampEvidence
            && HasNearbyTimeEvidence(read, matches);
    }

    if (allOrderEvidence && allMatches.count == RequiredReadCount) {
        NSArray *referenceVisualOrder = OrderedMarkerIndexes(allMatches.firstObject, YES);
        NSArray *referenceTreeOrder = OrderedMarkerIndexes(allMatches.firstObject, NO);
        for (NSArray<NSDictionary *> *matches in allMatches) {
            allOrderEvidence = allOrderEvidence
                && [OrderedMarkerIndexes(matches, YES) isEqualToArray:referenceVisualOrder]
                && [OrderedMarkerIndexes(matches, NO) isEqualToArray:referenceTreeOrder];
        }
    }

    NSString *messageText = allReadsComplete ? @"YES" : (anyMarkerFound ? @"PARTIAL" : @"NO");
    NSString *stableOrder = allOrderEvidence ? @"YES" : (anyMarkerFound ? @"PARTIAL" : @"NO");
    NSString *sender = allSenderEvidence ? @"PARTIAL" : @"NO";
    NSString *timestamp = allTimestampEvidence ? @"PARTIAL" : @"NO";
    NSString *status = @"NO-GO";
    if ([messageText isEqualToString:@"YES"] && [stableOrder isEqualToString:@"YES"]
        && ([sender isEqualToString:@"PARTIAL"] || [timestamp isEqualToString:@"PARTIAL"])) {
        status = @"GO";
    } else if ([messageText isEqualToString:@"YES"]
               || [messageText isEqualToString:@"PARTIAL"]) {
        status = @"CONDITIONAL";
    }

    NSArray<NSDictionary *> *firstMatches = allMatches.firstObject ?: @[];
    NSMutableArray<NSNumber *> *markerOccurrenceCounts = [NSMutableArray array];
    for (NSDictionary *match in firstMatches) {
        [markerOccurrenceCounts addObject:match[@"occurrenceCount"]];
    }
    return @{
        @"messageText": messageText,
        @"sender": sender,
        @"timestamp": timestamp,
        @"stableOrder": stableOrder,
        @"markerOccurrenceCounts": markerOccurrenceCounts,
        @"readCount": @(reads.count),
        @"boundsExceeded": @(boundsExceeded),
        @"scrollTest": @"NOT_PERFORMED",
        @"virtualizedList": @"UNKNOWN_NO_SCROLL_PERFORMED",
        @"status": boundsExceeded ? @"STOPPED_BOUNDS_EXCEEDED" : status,
    };
}

static NSDictionary *SyntheticItem(NSInteger order, NSString *value,
                                   double x, double y) {
    return @{
        @"order": @(order), @"parentOrder": @0,
        @"role": @"AXStaticText", @"subrole": [NSNull null],
        @"strings": value ? @[value] : @[], @"childCount": @0,
        @"hasFrame": @YES, @"x": @(x), @"y": @(y),
        @"width": @120, @"height": @24, @"truncatedAttributeCount": @0,
    };
}

static NSDictionary *ClassifierSelfTest(void) {
    NSArray<NSString *> *markers = @[
        @"AX_TEST_A_SYNTHETIC", @"AX_TEST_B_SYNTHETIC", @"AX_TEST_C_SYNTHETIC"
    ];
    NSArray *orderedRead = @[
        SyntheticItem(0, @"10:20", 80, 80),
        SyntheticItem(1, markers[0], 100, 100),
        SyntheticItem(2, markers[1], 620, 140),
        SyntheticItem(3, markers[2], 100, 180),
    ];
    NSArray *partialRead = @[SyntheticItem(0, markers[0], 100, 100)];
    NSArray *missingRead = @[SyntheticItem(0, @"10:20", 80, 80)];
    return @{
        @"ordered": ClassifyReads(@[orderedRead, orderedRead, orderedRead], markers,
                                    1000, NO),
        @"partial": ClassifyReads(@[partialRead, partialRead, partialRead], markers,
                                    1000, NO),
        @"missing": ClassifyReads(@[missingRead, missingRead, missingRead], markers,
                                    1000, NO),
    };
}

static NSDictionary *InspectTarget(NSDictionary *target,
                                    NSArray<NSString *> *markers,
                                    NSError **error) {
    if (!AXIsProcessTrusted()) {
        *error = [NSError errorWithDomain:@"AccessibilityGate2POC" code:2
            userInfo:@{NSLocalizedDescriptionKey: @"accessibility_not_authorized"}];
        return nil;
    }
    NSRunningApplication *application = RunningApplication(target);
    if (!application) {
        *error = [NSError errorWithDomain:@"AccessibilityGate2POC" code:3
            userInfo:@{NSLocalizedDescriptionKey: @"target_not_running"}];
        return nil;
    }
    AXUIElementRef applicationElement =
        AXUIElementCreateApplication(application.processIdentifier);
    CFTypeRef focusedValue = CopyAttribute(applicationElement, kAXFocusedWindowAttribute);
    CFRelease(applicationElement);
    if (!focusedValue || CFGetTypeID(focusedValue) != AXUIElementGetTypeID()) {
        if (focusedValue) CFRelease(focusedValue);
        *error = [NSError errorWithDomain:@"AccessibilityGate2POC" code:4
            userInfo:@{NSLocalizedDescriptionKey: @"focused_window_unavailable"}];
        return nil;
    }
    AXUIElementRef window = (AXUIElementRef)focusedValue;
    CGRect windowFrame = CGRectZero;
    if (!ElementFrame(window, &windowFrame)) {
        CFRelease(window);
        *error = [NSError errorWithDomain:@"AccessibilityGate2POC" code:5
            userInfo:@{NSLocalizedDescriptionKey: @"focused_window_frame_unavailable"}];
        return nil;
    }

    NSMutableArray *reads = [NSMutableArray array];
    BOOL anyBoundsExceeded = NO;
    for (NSUInteger index = 0; index < RequiredReadCount; index++) {
        BOOL boundsExceeded = NO;
        [reads addObject:Observe(window, windowFrame, &boundsExceeded)];
        anyBoundsExceeded = anyBoundsExceeded || boundsExceeded;
        if (index + 1 < RequiredReadCount) usleep(150000);
    }
    CFRelease(window);

    NSMutableDictionary *report = [ClassifyReads(
        reads, markers, windowFrame.size.width, anyBoundsExceeded
    ) mutableCopy];
    NSString *version = application.bundleURL
        ? [[NSBundle bundleWithURL:application.bundleURL]
            objectForInfoDictionaryKey:@"CFBundleShortVersionString"] : nil;
    [report addEntriesFromDictionary:@{
        @"schemaVersion": @1,
        @"mode": @"gate2_visible_window_read_only",
        @"target": target[@"key"],
        @"applicationVersion": version ?: [NSNull null],
        @"accessibilityTrusted": @YES,
        @"processDetected": @YES,
        @"windowDetected": @YES,
        @"rawStringsEmitted": @NO,
        @"safety": @{
            @"dedicatedTestChatAcknowledged": @YES,
            @"accessibilityPromptRequested": @NO,
            @"clientContentInspected": @YES,
            @"realPrivateChatsIntentionallyRead": @NO,
            @"scrollPerformed": @NO,
            @"uiMutationPerformed": @NO,
            @"dataPersistedByApp": @NO,
        },
    }];
    return report;
}

static BOOL EmitJSON(id value, NSError **error) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:value
        options:NSJSONWritingPrettyPrinted | NSJSONWritingSortedKeys error:error];
    if (!data) return NO;
    [[NSFileHandle fileHandleWithStandardOutput] writeData:data];
    [[NSFileHandle fileHandleWithStandardOutput]
        writeData:[@"\n" dataUsingEncoding:NSUTF8StringEncoding]];
    return YES;
}

static NSArray<NSString *> *MarkerArguments(NSArray<NSString *> *arguments) {
    NSMutableArray<NSString *> *markers = [NSMutableArray array];
    for (NSUInteger index = 0; index < arguments.count; index++) {
        if ([arguments[index] isEqualToString:@"--marker"]
            && index + 1 < arguments.count) {
            [markers addObject:arguments[++index]];
        }
    }
    return markers;
}

static BOOL MarkersAreSafe(NSArray<NSString *> *markers) {
    if (markers.count != RequiredMarkerCount
        || [NSSet setWithArray:markers].count != RequiredMarkerCount) return NO;
    for (NSString *marker in markers) {
        if (![marker hasPrefix:@"AX_TEST_"] || marker.length > 128) return NO;
    }
    return YES;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSArray<NSString *> *arguments = [[NSProcessInfo processInfo].arguments
            subarrayWithRange:NSMakeRange(1, MAX(0, argc - 1))];
        NSError *error = nil;

        if ([arguments isEqualToArray:@[@"--classifier-self-test"]]) {
            if (!EmitJSON(ClassifierSelfTest(), &error)) return 1;
            return 0;
        }
        if ([arguments isEqualToArray:@[@"--self-test"]]) {
            NSMutableArray *processes = [NSMutableArray array];
            for (NSDictionary *target in TargetSpecs()) {
                [processes addObject:ProcessReport(target)];
            }
            NSDictionary *report = @{
                @"schemaVersion": @1,
                @"mode": @"gate2_self_test",
                @"accessibilityTrusted": @(AXIsProcessTrusted()),
                @"contentRead": @NO,
                @"processes": processes,
                @"safety": @{
                    @"accessibilityPromptRequested": @NO,
                    @"clientContentInspected": @NO,
                    @"scrollPerformed": @NO,
                    @"uiMutationPerformed": @NO,
                },
            };
            if (!EmitJSON(report, &error)) return 1;
            return 0;
        }

        NSUInteger targetIndex = [arguments indexOfObject:@"--target"];
        NSString *targetKey = targetIndex != NSNotFound
            && targetIndex + 1 < arguments.count ? arguments[targetIndex + 1] : nil;
        NSDictionary *target = TargetForKey(targetKey);
        NSArray<NSString *> *markers = MarkerArguments(arguments);
        BOOL acknowledged = [arguments containsObject:
            @"--acknowledge-nonsensitive-test-chat"];
        if (!target || !acknowledged || !MarkersAreSafe(markers)) {
            fprintf(stderr,
                "usage: chat-accessibility-gate2-poc --self-test | "
                "--target qq|wechat --acknowledge-nonsensitive-test-chat "
                "--marker AX_TEST_A --marker AX_TEST_B --marker AX_TEST_C\n");
            return 2;
        }

        NSDictionary *report = InspectTarget(target, markers, &error);
        if (!report || !EmitJSON(report, &error)) {
            fprintf(stderr, "accessibility_gate2_poc_failed: %s\n",
                    error.localizedDescription.UTF8String);
            return 1;
        }
        return 0;
    }
}
