//
// Bridging header for the embedded CPython runtime.
//
// CPython.initialize is exposed by the Python.xcframework that
// Scripts/fetch_python_framework.sh unpacks. PythonKit calls it on first use;
// the declaration is here so Swift sees the symbol even when the framework is
// linked only through LIBRARY_SEARCH_PATHS.
//
#import <Foundation/Foundation.h>
#include <Python.h>
