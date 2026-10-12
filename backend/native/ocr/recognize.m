#import <Foundation/Foundation.h>
#import <ImageIO/ImageIO.h>
#import <Vision/Vision.h>

static void fail(const char *code) {
    fputs(code, stderr);
    exit(2);
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 2) fail("invalid_arguments");
        NSString *path = [NSString stringWithUTF8String:argv[1]];
        if (path == nil) fail("invalid_arguments");
        NSURL *url = [NSURL fileURLWithPath:path];
        CGImageSourceRef source = CGImageSourceCreateWithURL((__bridge CFURLRef)url, NULL);
        if (source == NULL) fail("invalid_image");
        CGImageRef image = CGImageSourceCreateImageAtIndex(source, 0, NULL);
        CFRelease(source);
        if (image == NULL) fail("invalid_image");

        VNRecognizeTextRequest *request = [[VNRecognizeTextRequest alloc] init];
        request.recognitionLevel = VNRequestTextRecognitionLevelAccurate;
        request.recognitionLanguages = @[@"zh-Hans", @"en-US"];
        request.usesLanguageCorrection = YES;
        VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithCGImage:image options:@{}];
        NSError *error = nil;
        BOOL succeeded = [handler performRequests:@[request] error:&error];
        CGImageRelease(image);
        if (!succeeded) {
            fprintf(stderr, "ocr_failed:%s:%ld", error.domain.UTF8String ?: "unknown", (long)error.code);
            return 2;
        }

        NSArray<VNRecognizedTextObservation *> *ordered = [request.results sortedArrayUsingComparator:^NSComparisonResult(VNRecognizedTextObservation *left, VNRecognizedTextObservation *right) {
            CGFloat leftTop = 1.0 - CGRectGetMaxY(left.boundingBox);
            CGFloat rightTop = 1.0 - CGRectGetMaxY(right.boundingBox);
            if (fabs(leftTop - rightTop) > 0.01) return leftTop < rightTop ? NSOrderedAscending : NSOrderedDescending;
            CGFloat leftX = CGRectGetMinX(left.boundingBox);
            CGFloat rightX = CGRectGetMinX(right.boundingBox);
            if (leftX == rightX) return NSOrderedSame;
            return leftX < rightX ? NSOrderedAscending : NSOrderedDescending;
        }];
        NSMutableArray<NSDictionary *> *lines = [NSMutableArray arrayWithCapacity:ordered.count];
        for (VNRecognizedTextObservation *observation in ordered) {
            VNRecognizedText *candidate = [[observation topCandidates:1] firstObject];
            if (candidate == nil) continue;
            CGRect box = observation.boundingBox;
            [lines addObject:@{
                @"text": candidate.string,
                @"box": @[@(box.origin.x), @(1.0 - CGRectGetMaxY(box)), @(box.size.width), @(box.size.height)],
                @"confidence": @(candidate.confidence),
            }];
        }
        NSData *output = [NSJSONSerialization dataWithJSONObject:@{@"lines": lines} options:0 error:&error];
        if (output == nil || fwrite(output.bytes, 1, output.length, stdout) != output.length) fail("output_failed");
        return 0;
    }
}
