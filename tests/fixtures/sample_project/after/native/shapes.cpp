// Added by the change: C++ types whose only reverse reference is to a variable.
//
// Every construct here is a way a C++ type is normally used, and in each one the type
// itself is referenced by an object rather than by a routine: `Box` by a local, `Extent`
// by a parameter, `Meter` by a member of `Gauge`. `ScanError` adds the throw/catch pair.
// A blast radius that stops at an entity it cannot name reports "nothing depends on this"
// for all four, which is why the impact walk has to see through them (requirement 9.5).

class Box {
public:
    explicit Box(int side) : side_(side) {}
    int area() const { return side_ * side_; }

private:
    int side_;
};

struct Extent {
    int width;
    int height;
};

class ScanError {
public:
    explicit ScanError(int code) : code_(code) {}
    int code() const { return code_; }

private:
    int code_;
};

class Meter {
public:
    int measure() const { return 1; }
};

class Gauge {
public:
    int read() const { return meter_.measure(); }

private:
    Meter meter_;
};

int box_area() {
    Box box(3);
    return box.area();
}

int extent_area(const Extent &size) {
    return size.width * size.height;
}

void raise_scan_error(int code) {
    throw ScanError(code);
}

int guarded_scan(int code) {
    try {
        raise_scan_error(code);
    } catch (const ScanError &err) {
        return err.code();
    }
    return 0;
}

// Two classes in one namespace, each used by a routine that knows nothing of the other.
// Defining the members inside a second `namespace lens { ... }` block is what makes
// Understand record `C Nameby` from each class to the namespace (measured); a blast radius
// that walks through the namespace inherits its whole user list, so both classes come back
// with both routines and a reviewer reads a risk that is not there.
namespace lens {
class Wide {
public:
    int span() const;
};

class Tight {
public:
    int span() const;
};
}  // namespace lens

namespace lens {
int Wide::span() const { return 2; }
int Tight::span() const { return 1; }
}  // namespace lens

int uses_wide() {
    lens::Wide wide;
    return wide.span();
}

int uses_tight() {
    lens::Tight tight;
    return tight.span();
}
