#import <AppKit/AppKit.h>
#import <ApplicationServices/ApplicationServices.h>
#import <unistd.h>

static const NSUInteger MaximumNodes = 5000;
static const NSUInteger MaximumDepth = 14;
static const NSUInteger MaximumAttributeCharacters = 1024;
static const NSUInteger MaximumObservedCharacters = 128 * 1024;
static const NSUInteger MaximumArrayStrings = 32;
static const NSUInteger RequiredMarkerCount = 3;

static NSDictionary *QQTarget(void) {
    return @{
        @"key": @"qq",
        @"bundleIdentifier": @"com.tencent.qq",
        @"paths": @[@"/Applications/QQ.app"],
    };
}

static NSRunningApplication *RunningQQ(void) {
    NSString *bundleIdentifier = QQTarget()[@"bundleIdentifier"];
    for (NSRunningApplication *application in NSWorkspace.sharedWorkspace.runningApplications) {
        if ([application.bundleIdentifier isEqualToString:bundleIdentifier]) return application;
    }
    return nil;
}

static NSString *QQVersion(NSRunningApplication *application) {
    if (application.bundleURL) {
        NSString *version = [[NSBundle bundleWithURL:application.bundleURL]
            objectForInfoDictionaryKey:@"CFBundleShortVersionString"];
        if (version.length) return version;
    }
    for (NSString *path in QQTarget()[@"paths"]) {
        if (![NSFileManager.defaultManager fileExistsAtPath:path]) continue;
        NSString *version = [[NSBundle bundleWithPath:path]
            objectForInfoDictionaryKey:@"CFBundleShortVersionString"];
        if (version.length) return version;
    }
    return nil;
}

static CFTypeRef CopyAttribute(AXUIElementRef element, CFStringRef attribute) {
    CFTypeRef value = NULL;
    AXError status = AXUIElementCopyAttributeValue(element, attribute, &value);
    return status == kAXErrorSuccess ? value : NULL;
}

static NSString *CopyBoundedStringAttribute(AXUIElementRef element,
                                             CFStringRef attribute,
                                             NSUInteger maximumCharacters) {
    CFTypeRef value = CopyAttribute(element, attribute);
    if (!value) return nil;
    NSString *result = nil;
    if (CFGetTypeID(value) == CFStringGetTypeID()) {
        NSString *string = (__bridge NSString *)value;
        result = string.length > maximumCharacters
            ? [string substringToIndex:maximumCharacters] : [string copy];
    }
    CFRelease(value);
    return result;
}

static NSArray<NSString *> *CopyAttributeNames(AXUIElementRef element) {
    CFArrayRef names = NULL;
    if (AXUIElementCopyAttributeNames(element, &names) != kAXErrorSuccess || !names) {
        return @[];
    }
    return CFBridgingRelease(names);
}

static NSArray<NSString *> *CopyParameterizedAttributeNames(AXUIElementRef element) {
    CFArrayRef names = NULL;
    if (AXUIElementCopyParameterizedAttributeNames(element, &names)
            != kAXErrorSuccess || !names) {
        return @[];
    }
    return CFBridgingRelease(names);
}

static CFIndex ChildCount(AXUIElementRef element) {
    CFIndex count = 0;
    if (AXUIElementGetAttributeValueCount(element, kAXChildrenAttribute, &count)
            != kAXErrorSuccess || count < 0) {
        return 0;
    }
    return count;
}

static NSArray *CopyChildElements(AXUIElementRef element, NSUInteger maximumCount) {
    if (maximumCount == 0) return @[];
    CFIndex available = ChildCount(element);
    if (available <= 0) return @[];
    CFArrayRef values = NULL;
    CFIndex requested = MIN(available, (CFIndex)maximumCount);
    if (AXUIElementCopyAttributeValues(element, kAXChildrenAttribute, 0, requested,
                                       &values) != kAXErrorSuccess || !values) {
        return @[];
    }
    return CFBridgingRelease(values);
}

static NSArray<NSString *> *FixedTextAttributeNames(void) {
    static NSArray<NSString *> *names;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        names = @[
            (__bridge NSString *)kAXValueAttribute,
            (__bridge NSString *)kAXTitleAttribute,
            (__bridge NSString *)kAXDescriptionAttribute,
            (__bridge NSString *)kAXHelpAttribute,
            (__bridge NSString *)kAXRoleDescriptionAttribute,
            (__bridge NSString *)kAXPlaceholderValueAttribute,
            (__bridge NSString *)kAXSelectedTextAttribute,
            (__bridge NSString *)kAXDocumentAttribute,
            (__bridge NSString *)kAXFilenameAttribute,
        ];
    });
    return names;
}

static BOOL IsTextRelatedAttributeName(NSString *name) {
    if ([FixedTextAttributeNames() containsObject:name]) return YES;
    NSString *lowercase = name.lowercaseString;
    for (NSString *fragment in @[
            @"value", @"title", @"description", @"help", @"text",
            @"label", @"placeholder", @"document", @"filename",
            @"string", @"content"]) {
        if ([lowercase containsString:fragment]) return YES;
    }
    return NO;
}

static BOOL IsTextRelatedParameterizedAttributeName(NSString *name) {
    return [name isEqualToString:(__bridge NSString *)kAXStringForRangeParameterizedAttribute]
        || [name isEqualToString:
            (__bridge NSString *)kAXAttributedStringForRangeParameterizedAttribute];
}

static NSSet<NSString *> *CandidateContainerRoles(void) {
    static NSSet<NSString *> *roles;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        roles = [NSSet setWithArray:@[
            @"AXStaticText", @"AXTextArea", @"AXScrollArea", @"AXGroup"
        ]];
    });
    return roles;
}

static void Increment(NSMutableDictionary<NSString *, NSNumber *> *dictionary,
                      NSString *key,
                      NSUInteger amount) {
    dictionary[key] = @([dictionary[key] unsignedIntegerValue] + amount);
}

static NSMutableDictionary *NewAttributeStats(void) {
    return [@{
        @"supportedNodeCount": @0,
        @"readableValueCount": @0,
        @"stringValueCount": @0,
        @"nonEmptyStringCount": @0,
        @"emptyStringCount": @0,
        @"totalCharacterCount": @0,
        @"maximumStringLength": @0,
        @"truncatedValueCount": @0,
        @"markerOccurrenceCounts": [NSMutableArray arrayWithArray:@[@0, @0, @0]],
    } mutableCopy];
}

static NSMutableDictionary *NewState(NSArray<NSString *> *markers) {
    NSMutableDictionary *textAttributes = [NSMutableDictionary dictionary];
    for (NSString *name in FixedTextAttributeNames()) {
        textAttributes[name] = NewAttributeStats();
    }
    textAttributes[(__bridge NSString *)kAXStringForRangeParameterizedAttribute]
        = NewAttributeStats();
    textAttributes[(__bridge NSString *)kAXAttributedStringForRangeParameterizedAttribute]
        = NewAttributeStats();
    return [@{
        @"markers": markers,
        @"roleDistribution": [NSMutableDictionary dictionary],
        @"subroleDistribution": [NSMutableDictionary dictionary],
        @"candidateContainerCounts": [NSMutableDictionary dictionaryWithDictionary:@{
            @"AXStaticText": @0,
            @"AXTextArea": @0,
            @"AXScrollArea": @0,
            @"AXGroup": @0,
        }],
        @"textAttributes": textAttributes,
        @"markerOccurrenceCounts": [NSMutableArray arrayWithArray:@[@0, @0, @0]],
        @"totalNodes": @0,
        @"maximumDepthObserved": @0,
        @"supportedTextAttributeNodeCount": @0,
        @"observedTextCharacters": @0,
        @"nodeLimitReached": @NO,
        @"depthLimitReached": @NO,
        @"textCharacterLimitReached": @NO,
    } mutableCopy];
}

static NSMutableDictionary *AttributeStats(NSMutableDictionary *state,
                                           NSString *name) {
    NSMutableDictionary *attributes = state[@"textAttributes"];
    if (!attributes[name]) attributes[name] = NewAttributeStats();
    return attributes[name];
}

static NSUInteger CountOccurrences(NSString *value, NSString *needle) {
    if (!value.length || !needle.length) return 0;
    NSUInteger count = 0;
    NSRange remaining = NSMakeRange(0, value.length);
    while (remaining.length > 0) {
        NSRange match = [value rangeOfString:needle
                                     options:NSLiteralSearch
                                       range:remaining];
        if (match.location == NSNotFound) break;
        count++;
        NSUInteger next = NSMaxRange(match);
        remaining = NSMakeRange(next, value.length - next);
    }
    return count;
}

static void AppendStringsFromValue(CFTypeRef value,
                                   NSMutableArray<NSString *> *strings,
                                   NSUInteger *remainingCharacters,
                                   NSUInteger depth,
                                   BOOL *truncated) {
    if (!value || depth > 2 || strings.count >= MaximumArrayStrings) {
        if (value && truncated) *truncated = YES;
        return;
    }
    CFTypeID type = CFGetTypeID(value);
    NSString *string = nil;
    if (type == CFStringGetTypeID()) {
        string = (__bridge NSString *)value;
    } else if (type == CFAttributedStringGetTypeID()) {
        string = (__bridge NSString *)CFAttributedStringGetString(
            (CFAttributedStringRef)value
        );
    }
    if (string) {
        if (string.length > *remainingCharacters) {
            [strings addObject:[string substringToIndex:*remainingCharacters]];
            *remainingCharacters = 0;
            if (truncated) *truncated = YES;
        } else {
            [strings addObject:[string copy]];
            *remainingCharacters -= string.length;
        }
        return;
    }
    if (type == CFArrayGetTypeID()) {
        CFArrayRef array = (CFArrayRef)value;
        CFIndex count = CFArrayGetCount(array);
        CFIndex limit = MIN(count, (CFIndex)MaximumArrayStrings);
        if (count > limit && truncated) *truncated = YES;
        for (CFIndex index = 0; index < limit && *remainingCharacters > 0; index++) {
            AppendStringsFromValue(CFArrayGetValueAtIndex(array, index), strings,
                                   remainingCharacters, depth + 1, truncated);
        }
    }
}

static void RecordSupportedAttribute(NSMutableDictionary *state, NSString *name) {
    NSMutableDictionary *stats = AttributeStats(state, name);
    stats[@"supportedNodeCount"] = @([stats[@"supportedNodeCount"]
        unsignedIntegerValue] + 1);
}

static void RecordValue(NSMutableDictionary *state,
                        NSString *attributeName,
                        CFTypeRef value) {
    NSMutableDictionary *stats = AttributeStats(state, attributeName);
    stats[@"readableValueCount"] = @([stats[@"readableValueCount"]
        unsignedIntegerValue] + 1);
    NSUInteger observed = [state[@"observedTextCharacters"] unsignedIntegerValue];
    if (observed >= MaximumObservedCharacters) {
        state[@"textCharacterLimitReached"] = @YES;
        return;
    }
    NSUInteger remaining = MIN(MaximumAttributeCharacters,
                               MaximumObservedCharacters - observed);
    BOOL truncated = NO;
    NSMutableArray<NSString *> *strings = [NSMutableArray array];
    AppendStringsFromValue(value, strings, &remaining, 0, &truncated);
    if (truncated) {
        stats[@"truncatedValueCount"] = @([stats[@"truncatedValueCount"]
            unsignedIntegerValue] + 1);
    }
    for (NSString *string in strings) {
        stats[@"stringValueCount"] = @([stats[@"stringValueCount"]
            unsignedIntegerValue] + 1);
        if (string.length == 0) {
            stats[@"emptyStringCount"] = @([stats[@"emptyStringCount"]
                unsignedIntegerValue] + 1);
        } else {
            stats[@"nonEmptyStringCount"] = @([stats[@"nonEmptyStringCount"]
                unsignedIntegerValue] + 1);
        }
        stats[@"totalCharacterCount"] = @([stats[@"totalCharacterCount"]
            unsignedIntegerValue] + string.length);
        stats[@"maximumStringLength"] = @(MAX(
            [stats[@"maximumStringLength"] unsignedIntegerValue], string.length
        ));
        state[@"observedTextCharacters"] = @(
            [state[@"observedTextCharacters"] unsignedIntegerValue] + string.length
        );

        NSMutableArray<NSNumber *> *attributeCounts = stats[@"markerOccurrenceCounts"];
        NSMutableArray<NSNumber *> *globalCounts = state[@"markerOccurrenceCounts"];
        [state[@"markers"] enumerateObjectsUsingBlock:^(
            NSString *marker, NSUInteger index, BOOL *stop
        ) {
            NSUInteger occurrences = CountOccurrences(string, marker);
            if (!occurrences) return;
            attributeCounts[index] = @([attributeCounts[index] unsignedIntegerValue]
                + occurrences);
            globalCounts[index] = @([globalCounts[index] unsignedIntegerValue]
                + occurrences);
        }];
    }
    if ([state[@"observedTextCharacters"] unsignedIntegerValue]
            >= MaximumObservedCharacters) {
        state[@"textCharacterLimitReached"] = @YES;
    }
}

static AXValueRef CopyVisibleRangeValue(AXUIElementRef element) {
    CFTypeRef value = CopyAttribute(element, kAXVisibleCharacterRangeAttribute);
    if (!value || CFGetTypeID(value) != AXValueGetTypeID()
        || AXValueGetType((AXValueRef)value) != kAXValueCFRangeType) {
        if (value) CFRelease(value);
        return NULL;
    }
    return (AXValueRef)value;
}

static void RecordNodeStructure(NSMutableDictionary *state,
                                NSString *role,
                                NSString *subrole,
                                NSUInteger depth) {
    NSString *safeRole = role.length ? role : @"NONE";
    NSString *safeSubrole = subrole.length ? subrole : @"NONE";
    Increment(state[@"roleDistribution"], safeRole, 1);
    Increment(state[@"subroleDistribution"], safeSubrole, 1);
    if ([CandidateContainerRoles() containsObject:safeRole]) {
        Increment(state[@"candidateContainerCounts"], safeRole, 1);
    }
    state[@"totalNodes"] = @([state[@"totalNodes"] unsignedIntegerValue] + 1);
    state[@"maximumDepthObserved"] = @(MAX(
        [state[@"maximumDepthObserved"] unsignedIntegerValue], depth
    ));
}

static void ObserveNodeText(AXUIElementRef element, NSMutableDictionary *state) {
    NSArray<NSString *> *names = CopyAttributeNames(element);
    NSMutableArray<NSString *> *textNames = [NSMutableArray array];
    for (NSString *name in names) {
        if (IsTextRelatedAttributeName(name)) [textNames addObject:name];
    }
    NSArray<NSString *> *parameterizedNames = CopyParameterizedAttributeNames(element);
    NSMutableArray<NSString *> *textParameterizedNames = [NSMutableArray array];
    for (NSString *name in parameterizedNames) {
        if (IsTextRelatedParameterizedAttributeName(name)) {
            [textParameterizedNames addObject:name];
        }
    }
    if (textNames.count || textParameterizedNames.count) {
        state[@"supportedTextAttributeNodeCount"] = @(
            [state[@"supportedTextAttributeNodeCount"] unsignedIntegerValue] + 1
        );
    }

    for (NSString *name in textNames) {
        RecordSupportedAttribute(state, name);
        if ([state[@"textCharacterLimitReached"] boolValue]) continue;
        CFTypeRef value = CopyAttribute(element, (__bridge CFStringRef)name);
        if (!value) continue;
        RecordValue(state, name, value);
        CFRelease(value);
    }

    if (!textParameterizedNames.count
        || [state[@"textCharacterLimitReached"] boolValue]) return;
    AXValueRef rangeValue = CopyVisibleRangeValue(element);
    for (NSString *name in textParameterizedNames) {
        RecordSupportedAttribute(state, name);
        if (!rangeValue || [state[@"textCharacterLimitReached"] boolValue]) continue;
        CFTypeRef value = NULL;
        AXError status = AXUIElementCopyParameterizedAttributeValue(
            element, (__bridge CFStringRef)name, rangeValue, &value
        );
        if (status != kAXErrorSuccess || !value) continue;
        RecordValue(state, name, value);
        CFRelease(value);
    }
    if (rangeValue) CFRelease(rangeValue);
}

static NSDictionary *FinalReport(NSMutableDictionary *state,
                                 NSString *mode,
                                 BOOL clientContentInspected) {
    NSArray<NSNumber *> *counts = [state[@"markerOccurrenceCounts"] copy];
    NSMutableArray<NSNumber *> *found = [NSMutableArray array];
    for (NSNumber *count in counts) [found addObject:@(count.unsignedIntegerValue > 0)];
    BOOL boundsExceeded = [state[@"nodeLimitReached"] boolValue]
        || [state[@"depthLimitReached"] boolValue]
        || [state[@"textCharacterLimitReached"] boolValue];
    return @{
        @"schemaVersion": @1,
        @"mode": mode,
        @"totalNodes": state[@"totalNodes"],
        @"maximumDepthObserved": state[@"maximumDepthObserved"],
        @"roleDistribution": state[@"roleDistribution"],
        @"subroleDistribution": state[@"subroleDistribution"],
        @"candidateContainerCounts": state[@"candidateContainerCounts"],
        @"supportedTextAttributeNodeCount": state[@"supportedTextAttributeNodeCount"],
        @"textAttributes": state[@"textAttributes"],
        @"observedTextCharacters": state[@"observedTextCharacters"],
        @"markerFound": found,
        @"markerOccurrenceCounts": counts,
        @"bounds": @{
            @"nodes": @(MaximumNodes),
            @"depth": @(MaximumDepth),
            @"perAttributeCharacters": @(MaximumAttributeCharacters),
            @"aggregateTextCharacters": @(MaximumObservedCharacters),
        },
        @"limitFlags": @{
            @"nodeLimitReached": state[@"nodeLimitReached"],
            @"depthLimitReached": state[@"depthLimitReached"],
            @"textCharacterLimitReached": state[@"textCharacterLimitReached"],
        },
        @"boundsExceeded": @(boundsExceeded),
        @"rawStringsEmitted": @NO,
        @"safety": @{
            @"accessibilityPromptRequested": @NO,
            @"clientContentInspected": @(clientContentInspected),
            @"realPrivateChatsIntentionallyRead": @NO,
            @"scrollPerformed": @NO,
            @"uiMutationPerformed": @NO,
            @"dataPersistedByApp": @NO,
        },
    };
}

static NSDictionary *ObserveHierarchy(AXUIElementRef window,
                                      NSArray<NSString *> *markers) {
    NSMutableDictionary *state = NewState(markers);
    NSMutableArray<NSDictionary *> *queue = [NSMutableArray arrayWithObject:@{
        @"element": (__bridge id)window,
        @"depth": @0,
    }];

    while (queue.count && [state[@"totalNodes"] unsignedIntegerValue] < MaximumNodes) {
        NSDictionary *queued = queue.firstObject;
        [queue removeObjectAtIndex:0];
        AXUIElementRef element = (__bridge AXUIElementRef)queued[@"element"];
        NSUInteger depth = [queued[@"depth"] unsignedIntegerValue];
        NSString *role = CopyBoundedStringAttribute(element, kAXRoleAttribute, 128);
        NSString *subrole = CopyBoundedStringAttribute(element, kAXSubroleAttribute, 128);
        RecordNodeStructure(state, role, subrole, depth);
        ObserveNodeText(element, state);

        CFIndex availableChildren = ChildCount(element);
        if (availableChildren <= 0) continue;
        if (depth >= MaximumDepth) {
            state[@"depthLimitReached"] = @YES;
            continue;
        }
        NSUInteger occupied = [state[@"totalNodes"] unsignedIntegerValue] + queue.count;
        NSUInteger remaining = occupied < MaximumNodes ? MaximumNodes - occupied : 0;
        if ((NSUInteger)availableChildren > remaining) state[@"nodeLimitReached"] = @YES;
        for (id child in CopyChildElements(element, remaining)) {
            [queue addObject:@{
                @"element": child,
                @"depth": @(depth + 1),
            }];
        }
    }
    if (queue.count) state[@"nodeLimitReached"] = @YES;
    return FinalReport(state, @"gate21_qq_visible_window_hierarchy_read_only", YES);
}

static void RecordSyntheticNode(NSMutableDictionary *state,
                                NSString *role,
                                NSString *subrole,
                                NSUInteger depth,
                                NSDictionary<NSString *, NSString *> *attributes) {
    RecordNodeStructure(state, role, subrole, depth);
    if (attributes.count) {
        state[@"supportedTextAttributeNodeCount"] = @(
            [state[@"supportedTextAttributeNodeCount"] unsignedIntegerValue] + 1
        );
    }
    [attributes enumerateKeysAndObjectsUsingBlock:^(
        NSString *name, NSString *value, BOOL *stop
    ) {
        RecordSupportedAttribute(state, name);
        RecordValue(state, name, (__bridge CFStringRef)value);
    }];
}

static NSDictionary *SyntheticSelfTest(void) {
    NSArray<NSString *> *markers = @[
        @"AX_TEST_A_GATE21", @"AX_TEST_B_GATE21", @"AX_TEST_C_GATE21"
    ];
    NSMutableDictionary *state = NewState(markers);
    RecordSyntheticNode(state, @"AXWindow", @"AXStandardWindow", 0, @{});
    RecordSyntheticNode(state, @"AXGroup", nil, 1, @{});
    RecordSyntheticNode(state, @"AXScrollArea", nil, 2, @{});
    RecordSyntheticNode(state, @"AXGroup", nil, 2, @{@"AXHelp": @""});
    RecordSyntheticNode(state, @"AXStaticText", nil, 3,
                        @{@"AXValue": markers[0]});
    RecordSyntheticNode(state, @"AXStaticText", nil, 3,
                        @{@"AXValue": markers[1]});
    RecordSyntheticNode(state, @"AXStaticText", nil, 3, @{
        @"AXValue": markers[2],
        @"AXTitle": @"SYNTHETIC_UNKNOWN_CONTENT",
    });
    return FinalReport(state, @"gate21_synthetic_hierarchy_self_test", NO);
}

static NSDictionary *InspectQQ(NSArray<NSString *> *markers, NSError **error) {
    if (!AXIsProcessTrusted()) {
        *error = [NSError errorWithDomain:@"AccessibilityGate21POC" code:2
            userInfo:@{NSLocalizedDescriptionKey: @"accessibility_not_authorized"}];
        return nil;
    }
    NSRunningApplication *application = RunningQQ();
    if (!application) {
        *error = [NSError errorWithDomain:@"AccessibilityGate21POC" code:3
            userInfo:@{NSLocalizedDescriptionKey: @"qq_not_running"}];
        return nil;
    }
    AXUIElementRef applicationElement =
        AXUIElementCreateApplication(application.processIdentifier);
    CFTypeRef focusedValue = CopyAttribute(applicationElement, kAXFocusedWindowAttribute);
    CFRelease(applicationElement);
    if (!focusedValue || CFGetTypeID(focusedValue) != AXUIElementGetTypeID()) {
        if (focusedValue) CFRelease(focusedValue);
        *error = [NSError errorWithDomain:@"AccessibilityGate21POC" code:4
            userInfo:@{NSLocalizedDescriptionKey: @"focused_window_unavailable"}];
        return nil;
    }
    NSDictionary *hierarchy = ObserveHierarchy((AXUIElementRef)focusedValue, markers);
    CFRelease(focusedValue);

    NSMutableDictionary *report = [hierarchy mutableCopy];
    NSArray<NSNumber *> *found = report[@"markerFound"];
    BOOL allMarkersFound = found.count == RequiredMarkerCount;
    for (NSNumber *value in found) allMarkersFound = allMarkersFound && value.boolValue;
    [report addEntriesFromDictionary:@{
        @"target": @"qq",
        @"applicationVersion": QQVersion(application) ?: [NSNull null],
        @"accessibilityTrusted": @YES,
        @"processDetected": @YES,
        @"windowDetected": @YES,
        @"allMarkersFound": @(allMarkersFound),
        @"status": allMarkersFound ? @"GO" : @"NO-GO",
        @"routeDecision": allMarkersFound
            ? @"MARKERS_EXPOSED_IN_ACCESSIBILITY"
            : @"NO-GO_CLOSE_ACCESSIBILITY_ROUTE",
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

        if ([arguments isEqualToArray:@[@"--synthetic-self-test"]]) {
            return EmitJSON(SyntheticSelfTest(), &error) ? 0 : 1;
        }
        if ([arguments isEqualToArray:@[@"--self-test"]]) {
            NSRunningApplication *application = RunningQQ();
            NSDictionary *report = @{
                @"schemaVersion": @1,
                @"mode": @"gate21_self_test",
                @"accessibilityTrusted": @(AXIsProcessTrusted()),
                @"contentRead": @NO,
                @"qq": @{
                    @"running": @(application != nil),
                    @"version": QQVersion(application) ?: [NSNull null],
                },
                @"safety": @{
                    @"accessibilityPromptRequested": @NO,
                    @"clientContentInspected": @NO,
                    @"scrollPerformed": @NO,
                    @"uiMutationPerformed": @NO,
                },
            };
            return EmitJSON(report, &error) ? 0 : 1;
        }

        NSUInteger targetIndex = [arguments indexOfObject:@"--target"];
        NSString *target = targetIndex != NSNotFound
            && targetIndex + 1 < arguments.count ? arguments[targetIndex + 1] : nil;
        NSArray<NSString *> *markers = MarkerArguments(arguments);
        BOOL acknowledged = [arguments containsObject:
            @"--acknowledge-nonsensitive-test-chat"];
        if (![target isEqualToString:@"qq"] || !acknowledged
            || !MarkersAreSafe(markers)) {
            fprintf(stderr,
                "usage: chat-accessibility-gate21-poc --self-test | "
                "--synthetic-self-test | --target qq "
                "--acknowledge-nonsensitive-test-chat "
                "--marker AX_TEST_A --marker AX_TEST_B --marker AX_TEST_C\n");
            return 2;
        }

        NSDictionary *report = InspectQQ(markers, &error);
        if (!report || !EmitJSON(report, &error)) {
            fprintf(stderr, "accessibility_gate21_poc_failed: %s\n",
                    error.localizedDescription.UTF8String);
            return 1;
        }
        return 0;
    }
}
