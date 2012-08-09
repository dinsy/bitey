# bind.py
#
# Bind LLVM functions to ctypes

from llvm.core import Module
import llvm.core
import llvm.ee
import ctypes
import io
import os
import sys

def map_llvm_to_ctypes(llvm_type, py_module):
    '''
    Map an LLVM type to an equivalent ctypes type. 
    '''
    kind = llvm_type.kind
    if kind == llvm.core.TYPE_INTEGER:
        ctype = getattr(ctypes,"c_int"+str(llvm_type.width))
    elif kind == llvm.core.TYPE_DOUBLE:
        ctype = ctypes.c_double
    elif kind == llvm.core.TYPE_FLOAT:
        ctype = ctypes.c_float
    elif kind == llvm.core.TYPE_POINTER:
        pointee = llvm_type.pointee
        p_kind = pointee.kind
        if p_kind == llvm.core.TYPE_INTEGER:
            width = pointee.width
            # Special case:  char * is mapped to strings
            if width == 8:
                ctype = ctypes.c_char_p
            else:
                ctype = ctypes.POINTER(map_llvm_to_ctypes(pointee, py_module))
        # Special case: void * mapped to c_void_p type
        elif p_kind == llvm.core.TYPE_VOID:
            ctype = ctypes.c_void_p
        else:
            ctype = ctypes.POINTER(map_llvm_to_ctypes(pointee, py_module))
    elif kind == llvm.core.TYPE_STRUCT:
        struct_name = llvm_type.name.split('.')[-1].encode('ascii')
        # If the named type is already known, return it
        struct_type = getattr(py_module, struct_name, None)
        
        if struct_type and issubclass(struct_type, ctypes.Structure):
            return struct_type


        # If there is an object with the name of the structure already present and it has
        # the field names specified, use those names to help out 
        if hasattr(struct_type, '_fields_'):
            names = struct_type._fields_
        else:
            names = [ "e"+str(n) for n in range(llvm_type.element_count) ]

        # Create a class definition for the type. It is critical that this
        # Take place before the handling of members to avoid issues with
        # self-referential data structures
        ctype = type(ctypes.Structure)(struct_name, (ctypes.Structure,),
                                       { '__module__' : py_module.__name__ })

        setattr(py_module, struct_name, ctype)

        # Resolve the structure fields
        fields = [ (name, map_llvm_to_ctypes(elem, py_module))
                   for name, elem in zip(names, llvm_type.elements) ]
        

        # Set the fields member of the type last.  The order is critical
        # to deal with self-referential structures.
        setattr(ctype, '_fields_', fields)
        
    elif kind == llvm.core.TYPE_VOID:
        ctype = None
    else:
        raise TypeError("Unknown type")
    return ctype

def make_ctypes_wrapper(engine, func, py_module):
    '''
    Create a ctypes wrapper around an LLVM function.
    engine is the LLVM execution engine.
    func is an LLVM function instance.
    '''
    args = func.type.pointee.args
    ret_type = func.type.pointee.return_type
    try:
        ret_ctype = map_llvm_to_ctypes(ret_type, py_module)
        args_ctypes = [map_llvm_to_ctypes(arg, py_module) for arg in args]
    except TypeError as e:
        if 'BITEYDEBUG' in os.environ:
            print e
        return None

    # Declare the ctypes function prototype
    functype = ctypes.CFUNCTYPE(ret_ctype, *args_ctypes)

    # Get the function point from the execution engine
    addr = engine.get_pointer_to_function(func)

    # Make a ctypes callable out of it
    return functype(addr)

def make_all_wrappers(llvm_module, engine, py_module):
    '''
    Build ctypes wrappers around an LLVM module and execution engine.
    py_module is an existing Python module that will be populated with
    the resulting wrappers.
    '''
    functions = [func for func in llvm_module.functions
                 if not func.name.startswith("_")
                 and not func.is_declaration
                 and func.linkage == llvm.core.LINKAGE_EXTERNAL]
    for func in functions:
        wrapper = make_ctypes_wrapper(engine, func, py_module)
        if wrapper:
            setattr(py_module, func.name, wrapper)
            wrapper.__name__ = func.name
        else:
            if 'BITEYDEBUG' in os.environ:
                sys.stderr.write("Couldn't wrap %s\n" % func.name)

def build_wrappers(bitcode, py_module):
    '''
    Given a byte-string of LLVM bitcode and a Python module,
    populate the module with ctypes bindings for public methods
    in the bitcode.
    '''
    llvm_module = Module.from_bitcode(io.BytesIO(bitcode))
    engine = llvm.ee.ExecutionEngine.new(llvm_module)
    make_all_wrappers(llvm_module, engine, py_module)
    setattr(py_module, '_llvm_module', llvm_module)
    setattr(py_module, '_llvm_engine', engine)

    

