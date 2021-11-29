"""
All things Dynamic Updates & Validation.

Hear me all ye who enter!
=========================

This is a module of disgusting hacks and monkey patching. Control flow
is all over the place and a comprised of hodgepodge of various strategies.
This is all because Argparse's internal parsing design (a) really,
really, REALLY wants to fail and sys.exit at the first error it
finds, and (b) does these program ending validations at seemingly random
points throughout its code base. Meaning, there is no single centralized
validation module, class, or function which could be customized. W

All that means is that it takes a fair amount of indirect, non-standard, and
gross monkey-patching to get Argparse to collect all its errors as it parses
rather than violently explode each time it finds one.

For additional background, see the original design here:
https://github.com/chriskiehl/Gooey/issues/755


"""
from argparse import ArgumentParser, _SubParsersAction
from functools import wraps
from typing import Union, Any, Mapping, Dict

from gooey.python_bindings.types import Success, Failure, Try
from gooey.python_bindings.argparse_to_json import is_subparser
from gooey.util.functional import lift, identity, merge
from gooey.gui.constants import VALUE_PLACEHOLDER


def check_value(registry: Dict[str, Exception], original_fn):
    """
    A Monkey Patch for `Argparse._check_value` which swallows
    the Exception and records the failure to the

    For certain argument types, Argparse calls a
    one-off `check_value` method. This method is inconvenient for us
    as it either returns nothing or throws an ArgumentException (thus leading
    to a sys.exit). Because our goal is to collect ALL
    errors for the entire parser, we must patch around this behavior.
    """
    @wraps(original_fn)
    def inner(self, action, value: Union[Any, Success, Failure]):
        def update_reg(_self, _action, _value):
            try:
                original_fn(_action, _value)
            except Exception as e:
                # IMPORTANT! note that this mutates the
                # reference that is passed in!
                registry[action.dest] = e

        # Inside of Argparse, `type_func` gets applied before the calls
        # to `check_value`. A such, depending on the type, this may already
        # be a lifted value.
        if isinstance(value, Success) and not isinstance(value, Failure):
            update_reg(self, action, value.value)
        else:
            update_reg(self, action, value)
    return inner



def patch_argument(parser, *args, **kwargs):
    """
    Mutates the supplied parser to append the arguments (args, kwargs) to
    the root parser and all subparsers.

    Example: `patch_argument(parser, '--ignore-gooey', action='store_true')

    This is used to punch additional cli arguments into the user's
    existing parser definition. By adding our arguments everywhere it allows
    us to use the `parse_args` machinery 'for free' without needing to
    worry about context shifts (like a repeated `dest` between subparsers).
    """
    parser.add_argument(*args, **kwargs)
    subparsers = list(filter(is_subparser, parser._actions))
    if subparsers:
        for sub in subparsers[0].choices.values():  # type: ignore
            patch_argument(sub, *args, **kwargs)
    return parser


def lift_actions_mutating(parser):
    """
    Mutates the supplied parser to lift all of its (likely) partial
    functions into total functions. See module docs for additional
    background. TL;DR: we have to "trick" Argparse into thinking
    every value is valid so that it doesn't short-circuit and sys.exit
    when it encounters a validation error. As such, we wrap everything
    in an Either/Try, and defer deciding the actual success/failure of
    the type transform until later in the execution when we have control.
    """
    for action in parser._actions:
        if issubclass(type(action), _SubParsersAction):
            for subparser in action.choices.values():
                lift_actions_mutating(subparser)
        else:
            action.type = lift(action.type or identity)


def collect_errors(error_registry: Dict[str, Exception], args: Dict[str, Try]) -> Dict[str, str]:
    """
    Merges all the errors from the Args mapping and error registry
    into a final dict.
    """
    # As is a theme throughout this module, to avoid Argparse
    # short-circuiting during parse-time, we pass a placeholder string
    # for required positional arguments which haven't yet been provided
    # by the user. So, what's happening here is that we're now collecting
    # all the args which have the placeholders so that we can flag them
    # all as required and missing.
    # Again, to be hyper clear, this is about being able to collect ALL
    # errors, versus just ONE error (Argparse default).
    required_but_missing = {k: 'This field is required'
                            for k, v in args.items()
                            if isinstance(v, Success) and v.value == VALUE_PLACEHOLDER}

    errors = {k: str(v.error) for k, v in args.items()
              if v is not None and isinstance(v, Failure)}
    # Secondary errors are those which get frustratingly applied by
    # Argparse in a way which can't be easily tracked with patching
    # or higher order functions. See: `check_value` for more details.
    secondary = {k: str(e) for k, e in error_registry.items() if e}
    return merge(required_but_missing, errors, secondary)


def monkey_patch_for_form_validation(error_registry: Dict[str, Exception], parser):
    """
    Applies all the crufty monkey patching required to
    process a validate_form event
    """
    lift_actions_mutating(parser)
    patch_argument(parser, '--gooey-validate-form', action='store_true')
    new_check_value = check_value(error_registry, parser._check_value)
    parser._check_value = new_check_value.__get__(parser, ArgumentParser)
    return parser

