#import <AppKit/AppKit.h>

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 2) return 2;
        NSImage *image = [[NSImage alloc] initWithSize:NSMakeSize(600, 200)];
        [image lockFocus];
        [[NSColor whiteColor] setFill];
        NSRectFill(NSMakeRect(0, 0, 600, 200));
        [@"OCR TEST 2026" drawAtPoint:NSMakePoint(50, 75)
            withAttributes:@{NSFontAttributeName: [NSFont boldSystemFontOfSize:48],
                             NSForegroundColorAttributeName: [NSColor blackColor]}];
        [image unlockFocus];
        NSBitmapImageRep *bitmap = [NSBitmapImageRep imageRepWithData:[image TIFFRepresentation]];
        NSData *png = [bitmap representationUsingType:NSBitmapImageFileTypePNG properties:@{}];
        return [png writeToFile:[NSString stringWithUTF8String:argv[1]] atomically:YES] ? 0 : 1;
    }
}
