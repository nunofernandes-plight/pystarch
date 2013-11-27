from functools import partial
from type_objects import Any, NoneType, Bool, Num, Str, List, Tuple, Set, \
    Dict, Function, Instance, Class, Undefined


def get_token(node):
    return node.__class__.__name__


def call_argtypes(call_node, context):
    types = []
    keyword_types = {}
    for arg in call_node.args:
        types.append(expression_type(arg, context))
    for keyword in call_node.keywords:
        keyword_types[keyword.arg] = expression_type(keyword.value, context)
    if call_node.starargs is not None:
        keyword_types['*args'] = expression_type(call_node.starargs, context)
    if call_node.kwargs is not None:
        keyword_types['**kwargs'] = expression_type(call_node.kwargs, context)
    return types, keyword_types


# "arguments" parameter is node.args for FunctionDef or Lambda
class Arguments(object):
    def __init__(self, arguments=None, context=None, decorator_list=[]):
        if arguments is None:
            return
        if context is None:
            context = Context()
        self.names = [arg.id for arg in arguments.args]
        self.min_count = len(arguments.args) - len(arguments.defaults)
        default_types = [expression_type(d, context)
                            for d in arguments.defaults]
        self.default_types = ([Any()] * self.min_count) + default_types
        self.explicit_types = self.get_explicit_argtypes(decorator_list)
        self.types = [explicit if explicit != Any() else default
            for explicit, default
            in zip(self.explicit_types, self.default_types)]
        self.vararg_name = arguments.vararg
        self.kwarg_name = arguments.kwarg

    @classmethod
    def copy_without_first_argument(klass, other_arguments):
        arguments = klass()
        arguments.names = other_arguments.names[1:]
        arguments.types = other_arguments.types[1:]
        arguments.default_types = other_arguments.default_types[1:]
        arguments.explicit_types = other_arguments.explicit_types[1:]
        arguments.min_count = max(0, other_arguments.min_count - 1)
        arguments.vararg_name = other_arguments.vararg
        arguments.kwarg_name = other_arguments.kwarg
        return arguments

    def get_dict(self):
        return dict(zip(self.names, self.types))

    def get_explicit_argtypes(self, decorator_list):
        types_decorator = [d for d in decorator_list
            if get_token(d) == 'Call' and show_node(d.func) == 'types']
        if len(types_decorator) == 1:
            argtypes, kwargtypes = call_argtypes(decorator, context)
        else:
            argtypes, kwargtypes = [], {}
        return argtypes + [kwargtypes.get(name, Any())
            for name in self.names[len(argtypes):]]

    def load_context(self, context):
        for name, argtype in self.kwargtypes.items():
            context.add_symbol(name, argtype)
        if self.vararg_name:
            context.add_symbol(self.vararg_name, Tuple())
        if self.kwarg_name:
            context.add_symbol(self.kwarg_name, Dict())

    def __str__(self):
        vararg = (', {0}: Tuple'.format(self.vararg_name)
            if self.vararg_name else '')
        kwarg = (', {0}: Dict'.format(self.kwarg_name)
            if self.kwarg_name else '')
        return (', '.join(name + ': ' + argtype for name, argtype
            in zip(self.argnames, self.argtypes)) + vararg + kwarg)


# adds assigned symbols to the current namespace, does not do validation
def assign(target, value, context, generator=False):
    value_type = expression_type(value, context)
    if generator:
        if hasattr(value_type, 'item_type'):
            assign_type = value_type.item_type
        elif (hasattr(value_type, 'item_types')
                and len(value_type.item_types) > 0):
            # assume that all elements have the same type
            assign_type = value_type.item_types[0]
        else:
            raise TypeError('Invalid type in generator')
    else:
        assign_type = value_type

    target_token = get_token(target)
    if target_token in ('Tuple', 'List'):
        if isinstance(assign_type, Tuple):
            if len(target.elts) != len(assign_type.item_types):
                raise ValueError('Tuple unpacking length mismatch')
            assignments = [(element.id, item_type) for element, item_type
                in zip(target.elts, assign_type.item_types)]
        elif isinstance(assign_type, List):
            element_type = assign_type.item_type
            assignments = [(element.id, element_type)
                for element in target.elts]
        else:
            raise TypeError('Invalid value type in assignment')
    elif target_token == 'Name':
        assignments = [(target.id, assign_type)]
    else:
        raise RuntimeError('Unrecognized assignment target ' + target_token)

    for name, assigned_type in assignments:
        context.add_symbol(name, assigned_type)
    return assignments


def comprehension_type(elements, generators, context):
    context.begin_namespace()
    for generator in generators:
        item_type = expression_type(generator.iter, context)
        assign(generator.target, generator.iter, context, generator=True)
    element_types = [expression_type(element, context) for element in elements]
    context.end_namespace()
    return element_types


# Note: "True" and "False" evalute to Bool because they are symbol
# names that have their types builtin to the default context. Similarly,
# "None" has type NoneType.
def expression_type(node, context):
    """
    This function determines the type of an expression, but does
    not do any type validation.
    """
    recur = partial(expression_type, context=context)
    token = get_token(node)
    if token == 'BoolOp':
        return recur(node.values[0])
    if token == 'BinOp':
        return Str() if (get_token(node.op) in ['Add', 'Mult']:
            and Str() in [recur(node.left), recur(node.right)]) else Num()
    if token == 'UnaryOp':
        return Bool() if get_token(node.op) == 'Not' else Num()
    if token == 'Lambda':
        return Function(Arguments(node.args, context), recur(node.body))
    if token == 'IfExp':
        return recur(node.body)
    if token == 'Dict':
        key_type = recur(node.keys[0]) if len(node.keys) > 0 else NoneType()
        value_type = recur(node.values[0]) if len(node.values) > 0
            else NoneType()
        return Dict(key_type, value_type)
    if token == 'Set':
        return Set(recur(node.elts[0]))
    if token == 'ListComp':
        element_type, = comprehension_type([node.elt], node.generators, context)
        return List(element_type)
    if token == 'SetComp':
        element_type, = comprehension_type([node.elt], node.generators, context)
        return Set(element_type)
    if token == 'DictComp':
        key_type, value_type = comprehension_type([node.key, node.value],
            node.generators, context)
        return Dict(key_type, value_type)
    if token == 'GeneratorExp':
        element_type, = comprehension_type([node.elt], node.generators, context)
        return List(element_type)
    if token == 'Yield':
        return recur(node.value)
    if token == 'Compare':
        return Bool()
    if token == 'Call':
        return recur(node.func).return_type
    if token == 'Repr':    # TODO: is Repr a Str?
        return Str()
    if token == 'Num':
        return Num()
    if token == 'Str':
        return Str()
    if token == 'Attribute':
        return context.get_attr_type(recur(node.value), node.attr)
    if token == 'Subscript':
        return recur(node.value)
    if token == 'Name':
        return context.get_type(node.id, Undefined())
    if token == 'List':
        element_type = recur(node.elts[0]) if len(node.elts) > 0 else NoneType()
        return List(element_type)
    if token == 'Tuple':
        item_types = [recur(element) for element in node.elts]
        return Tuple(item_types)
    raise Exception('evalute_type does not recognize ' + token)


def unit_test():
    context = Context()
    source = [
        '5 + 5',
        'not True',
        '+"abc"',
        '[a for a in (1, 2, 3)]',
        '[a for a in [1, 2, 3]]',
        '[a for a in {1, 2, 3}]',
        '[a * "a" for a in [1, 2, 3]]',
        '[{0: "a" * (a + 1)} for a in [1, 2, 3]]',
        '{a: b for a, b in [("x", 0), ("y", 1)]}',
    ]
    module = ast.parse('\n'.join(source))
    print(ast.dump(module))
    for i, statement in enumerate(module.body):
        expression = statement.value
        print(source[i] + ': ' + str(expression_type(expression, context)))


if __name__ == '__main__':
    import ast
    from context import Context
    unit_test()
